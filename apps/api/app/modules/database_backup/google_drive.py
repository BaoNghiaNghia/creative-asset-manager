from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.domain.providers.contracts import StorageProviderError
from app.modules.database_backup.service import VerifiedDatabaseBackup
from app.providers.google.storage import GoogleDriveAssetStorage

DATABASE_BACKUP_KIND = "database_backup_v1"
DEFAULT_UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class DatabaseBackupRemoteError(RuntimeError):

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class RemoteDatabaseBackup:
    remote_file_id: str
    name: str
    size_bytes: int
    created_at: datetime | None
    parents: tuple[str, ...]
    app_properties: dict[str, str]


class GoogleDriveDatabaseBackupStorage:

    def __init__(self,
                 credentials: GoogleDriveAssetStorage,
                 *,
                 folder_id: str,
                 transport: httpx.AsyncBaseTransport | None = None,
                 upload_chunk_bytes: int = DEFAULT_UPLOAD_CHUNK_BYTES) -> None:
        if not folder_id:
            raise ValueError("database backup Drive folder ID is required")
        if upload_chunk_bytes <= 0:
            raise ValueError(
                "database backup upload chunk size must be positive")
        self._credentials = credentials
        self._folder_id = folder_id
        self._transport = transport
        self._upload_chunk_bytes = upload_chunk_bytes

    @property
    def folder_id(self) -> str:
        return self._folder_id

    async def upload(self,
                     backup: VerifiedDatabaseBackup) -> RemoteDatabaseBackup:
        if not backup.path.is_file() or backup.size_bytes <= 0:
            raise DatabaseBackupRemoteError(
                "Database backup file is unavailable.")
        access_token = await self._get_access_token()
        metadata = {
            "name": backup.path.name,
            "parents": [self._folder_id],
            "mimeType": "application/octet-stream",
            "appProperties": {
                "cam_kind": DATABASE_BACKUP_KIND,
                "cam_sha256": backup.sha256
            }
        }
        try:
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=httpx.Timeout(90, connect=15, read=90),
                    transport=self._transport) as client:
                created = await client.post(
                    _DRIVE_UPLOAD_URL,
                    params={
                        "uploadType": "resumable",
                        "supportsAllDrives": "true",
                        "fields":
                        "id,name,size,parents,createdTime,appProperties"
                    },
                    headers={
                        "X-Upload-Content-Type": "application/octet-stream",
                        "X-Upload-Content-Length": str(backup.size_bytes)
                    },
                    json=metadata)
                self._raise_for_status(created)
                upload_url = created.headers.get("location")
                self._validate_upload_url(upload_url)
                remote_data: dict[str, Any] | None = None
                offset = 0
                with backup.path.open("rb") as handle:
                    while chunk := handle.read(self._upload_chunk_bytes):
                        end = offset + len(chunk) - 1
                        response = await client.put(
                            upload_url,
                            headers={
                                "Content-Type":
                                "application/octet-stream",
                                "Content-Length":
                                str(len(chunk)),
                                "Content-Range":
                                f"bytes {offset}-{end}/{backup.size_bytes}"
                            },
                            content=chunk)
                        if response.status_code in {200, 201}:
                            remote_data = response.json()
                        elif response.status_code != 308:
                            self._raise_for_status(response)
                            raise DatabaseBackupRemoteError(
                                "Drive resumable upload failed.")
                        offset = end + 1
                if offset != backup.size_bytes or remote_data is None:
                    raise DatabaseBackupRemoteError(
                        "Drive resumable upload did not complete.",
                        retryable=True)
                return self._remote_from_payload(remote_data)
        except DatabaseBackupRemoteError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DatabaseBackupRemoteError("Drive backup upload failed.",
                                            retryable=True) from exc

    async def verify(self, remote_file_id: str,
                     expected_size_bytes: int) -> RemoteDatabaseBackup:
        remote = await self._get(remote_file_id)
        if remote.size_bytes != expected_size_bytes:
            raise DatabaseBackupRemoteError(
                "Drive backup size verification failed.")
        if self._folder_id not in remote.parents or remote.app_properties.get(
                "cam_kind") != DATABASE_BACKUP_KIND:
            raise DatabaseBackupRemoteError(
                "Drive backup verification failed.")
        return remote

    async def list_managed_backups(self) -> list[RemoteDatabaseBackup]:
        access_token = await self._get_access_token()
        quote = chr(39)
        query = (
            f"{quote}{self._escape_query(self._folder_id)}{quote} in parents and trashed = false and "
            f"appProperties has {{ key={quote}cam_kind{quote} and value={quote}{DATABASE_BACKUP_KIND}{quote} }}"
        )
        page_token: str | None = None
        backups: list[RemoteDatabaseBackup] = []
        try:
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=httpx.Timeout(60, connect=15, read=60),
                    transport=self._transport) as client:
                while True:
                    params: dict[str, str] = {
                        "q": query,
                        "spaces": "drive",
                        "pageSize": "100",
                        "fields":
                        "nextPageToken,files(id,name,size,parents,createdTime,appProperties)",
                        "supportsAllDrives": "true",
                        "includeItemsFromAllDrives": "true"
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    response = await client.get(_DRIVE_FILES_URL,
                                                params=params)
                    self._raise_for_status(response)
                    payload = response.json()
                    backups.extend(
                        self._remote_from_payload(item)
                        for item in payload.get("files") or [])
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        return backups
        except DatabaseBackupRemoteError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DatabaseBackupRemoteError("Drive backup listing failed.",
                                            retryable=True) from exc

    async def delete_managed_backup(self,
                                    remote: RemoteDatabaseBackup) -> None:
        if self._folder_id not in remote.parents or remote.app_properties.get(
                "cam_kind") != DATABASE_BACKUP_KIND:
            raise DatabaseBackupRemoteError(
                "Refusing to delete an unmanaged Drive file.")
        access_token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=httpx.Timeout(60, connect=15, read=60),
                    transport=self._transport) as client:
                response = await client.delete(
                    f"{_DRIVE_FILES_URL}/{remote.remote_file_id}",
                    params={"supportsAllDrives": "true"})
                self._raise_for_status(response)
        except DatabaseBackupRemoteError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DatabaseBackupRemoteError("Drive backup deletion failed.",
                                            retryable=True) from exc

    async def _get(self, remote_file_id: str) -> RemoteDatabaseBackup:
        access_token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=httpx.Timeout(60, connect=15, read=60),
                    transport=self._transport) as client:
                response = await client.get(
                    f"{_DRIVE_FILES_URL}/{remote_file_id}",
                    params={
                        "fields":
                        "id,name,size,parents,createdTime,appProperties",
                        "supportsAllDrives": "true"
                    })
                self._raise_for_status(response)
                return self._remote_from_payload(response.json())
        except DatabaseBackupRemoteError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DatabaseBackupRemoteError(
                "Drive backup verification failed.", retryable=True) from exc

    async def _get_access_token(self) -> str:
        try:
            return await self._credentials.get_access_token()
        except StorageProviderError as exc:
            raise DatabaseBackupRemoteError(
                "Managed Drive backup credentials are unavailable.",
                retryable=exc.retryable,
            ) from exc

    @staticmethod
    def _remote_from_payload(payload: dict[str, Any]) -> RemoteDatabaseBackup:
        remote_file_id = str(payload.get("id") or "")
        if not remote_file_id:
            raise DatabaseBackupRemoteError(
                "Drive response returned no file ID.", retryable=True)
        try:
            size_bytes = int(payload.get("size"))
        except (TypeError, ValueError) as exc:
            raise DatabaseBackupRemoteError(
                "Drive response returned an invalid file size.",
                retryable=True) from exc
        raw_created_at = payload.get("createdTime")
        created_at: datetime | None = None
        if isinstance(raw_created_at, str) and raw_created_at:
            try:
                created_at = datetime.fromisoformat(
                    raw_created_at.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        properties = payload.get("appProperties") or {}
        return RemoteDatabaseBackup(
            remote_file_id=remote_file_id,
            name=str(payload.get("name") or remote_file_id),
            size_bytes=size_bytes,
            created_at=created_at,
            parents=tuple(
                str(parent) for parent in payload.get("parents") or []),
            app_properties={
                str(key): str(value)
                for key, value in properties.items()
            })

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace(chr(39), "\\" + chr(39))

    @staticmethod
    def _validate_upload_url(value: str | None) -> None:
        if not value:
            raise DatabaseBackupRemoteError(
                "Drive returned no resumable upload URL.", retryable=True)
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
                hostname == "googleapis.com"
                or hostname.endswith(".googleapis.com")):
            raise DatabaseBackupRemoteError(
                "Drive returned an unsafe resumable upload URL.")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise DatabaseBackupRemoteError("Drive backup request failed.",
                                        retryable=response.status_code
                                        in {408, 425, 429, 500, 502, 503, 504})


def build_database_backup_storage(
    settings: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None
) -> GoogleDriveDatabaseBackupStorage:
    folder_id = str(
        getattr(settings, "DATABASE_BACKUP_DRIVE_FOLDER_ID", "")
        or "").strip()
    root_folder_id = str(
        getattr(settings, "GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID", "")
        or "").strip()
    if not folder_id:
        raise DatabaseBackupRemoteError(
            "Database backup Drive folder is not configured.")
    credentials = GoogleDriveAssetStorage(
        getattr(settings, "GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN", None),
        root_folder_id=root_folder_id,
        refresh_token=getattr(settings, "GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN",
                              None),
        client_id=getattr(settings, "GOOGLE_CLIENT_ID", None),
        client_secret=getattr(settings, "GOOGLE_CLIENT_SECRET", None),
        transport=transport,
    )
    return GoogleDriveDatabaseBackupStorage(credentials,
                                            folder_id=folder_id,
                                            transport=transport)
