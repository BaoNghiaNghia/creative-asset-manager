from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.domain.providers.contracts import StorageProviderError
from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, DatabaseBackupRemoteError, GoogleDriveDatabaseBackupStorage, RemoteDatabaseBackup
from app.modules.database_backup.retention import DatabaseBackupRetentionService
from app.modules.database_backup.service import VerifiedDatabaseBackup
from app.providers.google.storage import GoogleDriveAssetStorage


class DatabaseBackupGoogleDriveTest(unittest.IsolatedAsyncioTestCase):

    def _backup(self,
                directory: str,
                content: bytes = b"abcdefghij") -> VerifiedDatabaseBackup:
        path = Path(directory) / "cam-db-20260819-220000+0700.dump"
        path.write_bytes(content)
        return VerifiedDatabaseBackup(path=path,
                                      created_at=datetime(2026,
                                                          8,
                                                          19,
                                                          tzinfo=timezone.utc),
                                      size_bytes=len(content),
                                      sha256="a" * 64)

    async def test_rejected_managed_credential_is_wrapped_and_fails_closed(
            self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url),
                             "https://oauth2.googleapis.com/token")
            return httpx.Response(400, json={"error": "invalid_grant"})

        credentials = GoogleDriveAssetStorage(
            root_folder_id="managed-root",
            refresh_token="revoked-refresh-token",
            client_id="client-id",
            client_secret="client-secret",
            transport=httpx.MockTransport(handler),
        )
        storage = GoogleDriveDatabaseBackupStorage(
            credentials,
            folder_id="backup-folder",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(DatabaseBackupRemoteError) as context:
            await storage.list_managed_backups()
        self.assertFalse(context.exception.retryable)
        self.assertIsInstance(context.exception.__cause__,
                              StorageProviderError)

    async def test_resumable_upload_is_chunked_and_remote_verified_with_managed_refresh(
            self) -> None:
        uploads: list[tuple[str, bytes]] = []
        token_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if str(request.url) == "https://oauth2.googleapis.com/token":
                token_calls += 1
                return httpx.Response(200,
                                      json={
                                          "access_token": "short-lived",
                                          "expires_in": 3600
                                      })
            if request.method == "POST":
                payload = json.loads((await request.aread()).decode())
                self.assertEqual(payload["parents"], ["backup-folder"])
                self.assertEqual(payload["appProperties"]["cam_kind"],
                                 DATABASE_BACKUP_KIND)
                return httpx.Response(
                    200,
                    headers={
                        "location":
                        "https://www.googleapis.com/upload/session-1"
                    })
            if request.method == "PUT":
                uploads.append((request.headers["Content-Range"], await
                                request.aread()))
                if len(uploads) < 3:
                    return httpx.Response(308)
                return httpx.Response(200,
                                      json={
                                          "id": "remote-1",
                                          "name": "backup.dump",
                                          "size": "10",
                                          "parents": ["backup-folder"],
                                          "appProperties": {
                                              "cam_kind": DATABASE_BACKUP_KIND
                                          }
                                      })
            if request.method == "GET":
                return httpx.Response(200,
                                      json={
                                          "id": "remote-1",
                                          "name": "backup.dump",
                                          "size": "10",
                                          "parents": ["backup-folder"],
                                          "createdTime":
                                          "2026-08-19T15:00:00Z",
                                          "appProperties": {
                                              "cam_kind": DATABASE_BACKUP_KIND
                                          }
                                      })
            raise AssertionError(request.method)

        transport = httpx.MockTransport(handler)
        credentials = GoogleDriveAssetStorage(root_folder_id="managed-root",
                                              refresh_token="refresh-token",
                                              client_id="client-id",
                                              client_secret="client-secret",
                                              transport=transport)
        storage = GoogleDriveDatabaseBackupStorage(credentials,
                                                   folder_id="backup-folder",
                                                   transport=transport,
                                                   upload_chunk_bytes=4)
        with tempfile.TemporaryDirectory() as directory:
            backup = self._backup(directory)
            uploaded = await storage.upload(backup)
            verified = await storage.verify(uploaded.remote_file_id,
                                            backup.size_bytes)
        self.assertEqual(token_calls, 1)
        self.assertEqual(uploads, [("bytes 0-3/10", b"abcd"),
                                   ("bytes 4-7/10", b"efgh"),
                                   ("bytes 8-9/10", b"ij")])
        self.assertEqual(verified.remote_file_id, "remote-1")

    async def test_remote_size_mismatch_fails_closed(self) -> None:

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            return httpx.Response(200,
                                  json={
                                      "id": "remote-1",
                                      "name": "backup.dump",
                                      "size": "9",
                                      "parents": ["backup-folder"],
                                      "appProperties": {
                                          "cam_kind": DATABASE_BACKUP_KIND
                                      }
                                  })

        transport = httpx.MockTransport(handler)
        credentials = GoogleDriveAssetStorage("token",
                                              root_folder_id="managed-root",
                                              transport=transport)
        storage = GoogleDriveDatabaseBackupStorage(credentials,
                                                   folder_id="backup-folder",
                                                   transport=transport)
        with self.assertRaises(DatabaseBackupRemoteError):
            await storage.verify("remote-1", 10)

    async def test_listing_is_paged_and_delete_refuses_wrong_scope(
            self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("pageToken") == "next":
                return httpx.Response(200,
                                      json={
                                          "files": [{
                                              "id":
                                              "second",
                                              "name":
                                              "second.dump",
                                              "size":
                                              "1",
                                              "parents": ["backup-folder"],
                                              "appProperties": {
                                                  "cam_kind":
                                                  DATABASE_BACKUP_KIND
                                              }
                                          }]
                                      })
            return httpx.Response(200,
                                  json={
                                      "nextPageToken":
                                      "next",
                                      "files": [{
                                          "id": "first",
                                          "name": "first.dump",
                                          "size": "1",
                                          "parents": ["backup-folder"],
                                          "appProperties": {
                                              "cam_kind": DATABASE_BACKUP_KIND
                                          }
                                      }]
                                  })

        transport = httpx.MockTransport(handler)
        credentials = GoogleDriveAssetStorage("token",
                                              root_folder_id="managed-root",
                                              transport=transport)
        storage = GoogleDriveDatabaseBackupStorage(credentials,
                                                   folder_id="backup-folder",
                                                   transport=transport)
        listed = await storage.list_managed_backups()
        self.assertEqual([item.remote_file_id for item in listed],
                         ["first", "second"])
        self.assertIn("backup-folder", requests[0].url.params["q"])
        self.assertIn("cam_kind", requests[0].url.params["q"])
        unsafe = RemoteDatabaseBackup("unsafe", "unsafe", 1, None,
                                      ("other-folder", ),
                                      {"cam_kind": DATABASE_BACKUP_KIND})
        with self.assertRaises(DatabaseBackupRemoteError):
            await storage.delete_managed_backup(unsafe)
        self.assertEqual(len(requests), 2)


class _RetentionStorage:
    folder_id = "backup-folder"

    def __init__(self, backups: list[RemoteDatabaseBackup]) -> None:
        self.backups = backups
        self.deleted: list[str] = []

    async def list_managed_backups(self) -> list[RemoteDatabaseBackup]:
        return self.backups

    async def delete_managed_backup(self,
                                    remote: RemoteDatabaseBackup) -> None:
        self.deleted.append(remote.remote_file_id)


class DatabaseBackupRetentionTest(unittest.IsolatedAsyncioTestCase):

    async def test_exactly_six_recent_files_delete_none(self) -> None:
        storage = _RetentionStorage(
            [self._remote(f"remote-{index}", index) for index in range(6)])
        result = await DatabaseBackupRetentionService().prune_after_verified_upload(
            storage,
            protected_remote_file_id="remote-0",
            now=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(storage.deleted, [])
        self.assertEqual(result.remote_backup_count, 6)

    def _remote(self,
                name: str,
                age_days: int,
                *,
                parent: str = "backup-folder",
                kind: str = DATABASE_BACKUP_KIND) -> RemoteDatabaseBackup:
        return RemoteDatabaseBackup(
            name, name, 1,
            datetime(2026, 8, 19, tzinfo=timezone.utc) -
            timedelta(days=age_days), (parent, ), {"cam_kind": kind})

    async def test_keeps_six_recent_files_and_prunes_oldest_seventh_after_age(
            self) -> None:
        storage = _RetentionStorage(
            [self._remote(f"remote-{index}", index) for index in range(7)])
        result = await DatabaseBackupRetentionService(
        ).prune_after_verified_upload(storage,
                                      protected_remote_file_id="remote-0",
                                      now=datetime(2026,
                                                   8,
                                                   19,
                                                   tzinfo=timezone.utc))
        self.assertEqual(storage.deleted, ["remote-6"])
        self.assertEqual(result.age_pruned, 0)
        self.assertEqual(result.count_pruned, 1)

    async def test_prunes_expired_first_and_never_touches_other_scope(
            self) -> None:
        storage = _RetentionStorage([
            self._remote("expired", 22),
            self._remote("recent", 1),
            self._remote("wrong-parent", 40, parent="other"),
            self._remote("wrong-kind", 40, kind="not-backup")
        ])
        result = await DatabaseBackupRetentionService(
        ).prune_after_verified_upload(storage,
                                      protected_remote_file_id="recent",
                                      now=datetime(2026,
                                                   8,
                                                   19,
                                                   tzinfo=timezone.utc))
        self.assertEqual(storage.deleted, ["expired"])
        self.assertEqual(result.age_pruned, 1)
        self.assertEqual(result.count_pruned, 0)
