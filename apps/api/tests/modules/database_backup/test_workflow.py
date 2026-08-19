from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, DatabaseBackupRemoteError, RemoteDatabaseBackup
from app.modules.database_backup.retention import DatabaseBackupRetentionResult
from app.modules.database_backup.service import VerifiedDatabaseBackup
from app.modules.database_backup.workflow import DatabaseBackupWorkflow


class _Local:

    def __init__(self, backup: VerifiedDatabaseBackup) -> None:
        self.backup = backup
        self.cleaned = 0
        self.locked = 0

    @contextmanager
    def exclusive_lock(self):
        self.locked += 1
        yield

    def create_verified_dump_locked(self) -> VerifiedDatabaseBackup:
        return self.backup

    def cleanup_verified_backup(self, backup: VerifiedDatabaseBackup) -> None:
        self.cleaned += 1
        backup.path.unlink(missing_ok=True)


class _Remote:
    folder_id = "backup-folder"

    def __init__(self,
                 *,
                 upload_error: Exception | None = None,
                 verify_error: Exception | None = None) -> None:
        self.upload_error = upload_error
        self.verify_error = verify_error
        self.uploads = 0
        self.verifications = 0
        self.list_calls = 0

    async def upload(self,
                     backup: VerifiedDatabaseBackup) -> RemoteDatabaseBackup:
        self.uploads += 1
        if self.upload_error:
            raise self.upload_error
        return RemoteDatabaseBackup("remote-1", backup.path.name,
                                    backup.size_bytes,
                                    datetime.now(timezone.utc),
                                    ("backup-folder", ),
                                    {"cam_kind": DATABASE_BACKUP_KIND})

    async def verify(self, remote_file_id: str,
                     expected_size_bytes: int) -> RemoteDatabaseBackup:
        self.verifications += 1
        if self.verify_error:
            raise self.verify_error
        return RemoteDatabaseBackup(remote_file_id, "backup.dump",
                                    expected_size_bytes,
                                    datetime.now(timezone.utc),
                                    ("backup-folder", ),
                                    {"cam_kind": DATABASE_BACKUP_KIND})


class _Retention:

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def prune_after_verified_upload(
            self, storage, *,
            protected_remote_file_id: str) -> DatabaseBackupRetentionResult:
        self.calls += 1
        if self.error:
            raise self.error
        return DatabaseBackupRetentionResult(1, 0, 0, 1)


class DatabaseBackupWorkflowTest(unittest.IsolatedAsyncioTestCase):

    def _local(self) -> tuple[_Local, tempfile.TemporaryDirectory[str]]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "cam-db.dump"
        path.write_bytes(b"dump")
        return _Local(
            VerifiedDatabaseBackup(path, datetime.now(timezone.utc), 4,
                                   "a" * 64)), directory

    async def test_upload_failure_never_starts_retention_and_cleans_local_file(
            self) -> None:
        local, directory = self._local()
        try:
            remote = _Remote(upload_error=DatabaseBackupRemoteError(
                "upload unavailable", retryable=True))
            retention = _Retention()
            with self.assertRaises(DatabaseBackupRemoteError):
                await DatabaseBackupWorkflow(local, remote, retention).run()
            self.assertEqual(retention.calls, 0)
            self.assertEqual(local.cleaned, 1)
            self.assertFalse(local.backup.path.exists())
        finally:
            directory.cleanup()

    async def test_remote_verification_failure_never_starts_retention_and_cleans_local_file(
            self) -> None:
        local, directory = self._local()
        try:
            remote = _Remote(
                verify_error=DatabaseBackupRemoteError("size mismatch"))
            retention = _Retention()
            with self.assertRaises(DatabaseBackupRemoteError):
                await DatabaseBackupWorkflow(local, remote, retention).run()
            self.assertEqual(local.locked, 1)
            self.assertEqual(retention.calls, 0)
            self.assertEqual(local.cleaned, 1)
            self.assertFalse(local.backup.path.exists())
        finally:
            directory.cleanup()

    async def test_prune_failure_preserves_verified_remote_and_cleans_local_file(
            self) -> None:
        local, directory = self._local()
        try:
            remote = _Remote()
            retention = _Retention(error=DatabaseBackupRemoteError(
                "prune unavailable", retryable=True))
            with self.assertRaises(DatabaseBackupRemoteError):
                await DatabaseBackupWorkflow(local, remote, retention).run()
            self.assertEqual(remote.uploads, 1)
            self.assertEqual(remote.verifications, 1)
            self.assertEqual(retention.calls, 1)
            self.assertEqual(local.cleaned, 1)
            self.assertFalse(local.backup.path.exists())
        finally:
            directory.cleanup()
