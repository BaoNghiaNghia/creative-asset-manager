from __future__ import annotations

from pathlib import PurePath
from urllib.parse import urlsplit

import httpx

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    StoreAssetInput,
    StoredAsset,
    StoredMetadataSidecar,
    StoreMetadataSidecarInput,
)


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveAssetStorage(AssetStorageProvider):
    """Managed storage adapter; credentials are independent from Source Drive."""

    provider_name = "google_drive_managed"

    def __init__(
        self,
        storage_access_token: str,
        *,
        root_folder_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not storage_access_token:
            raise ValueError("storage access token is required")
        if not root_folder_id:
            raise ValueError("managed storage root folder ID is required")
        self._access_token = storage_access_token
        self._root_folder_id = root_folder_id
        self._transport = transport

    async def store_asset(self, input: StoreAssetInput) -> StoredAsset:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(60, connect=10, read=60),
            transport=self._transport,
        ) as client:
            existing = await self._find_existing(client, input)
            if existing is not None:
                return self._stored(input, existing)

            suffix = PurePath(input.filename or "").suffix.lower()
            filename = f"{input.content_hash}{suffix}" if suffix else input.content_hash
            metadata = {
                "name": filename,
                "parents": [self._root_folder_id],
                "appProperties": {
                    "cam_tenant_id": input.tenant_id,
                    "cam_asset_id": input.asset_id,
                    "cam_content_hash": input.content_hash,
                },
            }
            create_response = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={
                    "uploadType": "resumable",
                    "supportsAllDrives": "true",
                    "fields": "id,parents,webViewLink,size",
                },
                headers={
                    "X-Upload-Content-Type": input.content_type or "application/octet-stream",
                    **(
                        {"X-Upload-Content-Length": str(input.size_bytes)}
                        if input.size_bytes is not None
                        else {}
                    ),
                },
                json=metadata,
            )
            self._raise_for_status(create_response)
            upload_url = create_response.headers.get("location")
            self._validate_upload_url(upload_url)
            upload_headers = {"Content-Type": input.content_type or "application/octet-stream"}
            if input.size_bytes is not None:
                upload_headers["Content-Length"] = str(input.size_bytes)
            upload_response = await client.put(
                upload_url,
                headers=upload_headers,
                content=input.body,
            )
            self._raise_for_status(upload_response)
            data = upload_response.json()
            if not data.get("id"):
                raise StorageProviderError("Google Drive upload returned no file ID", retryable=True)
            return self._stored(input, data)

    async def store_metadata_sidecar(
        self, input: StoreMetadataSidecarInput
    ) -> StoredMetadataSidecar:
        raise NotImplementedError("metadata sidecar export is introduced in Step 19")

    async def _find_existing(
        self, client: httpx.AsyncClient, input: StoreAssetInput
    ) -> dict | None:
        query = (
            f"'{_escape_query(self._root_folder_id)}' in parents and trashed = false and "
            f"appProperties has {{ key='cam_tenant_id' and value='{_escape_query(input.tenant_id)}' }} and "
            f"appProperties has {{ key='cam_asset_id' and value='{_escape_query(input.asset_id)}' }}"
        )
        response = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": query,
                "spaces": "drive",
                "pageSize": "2",
                "fields": "files(id,name,parents,webViewLink,size,appProperties)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        self._raise_for_status(response)
        files = response.json().get("files") or []
        if len(files) > 1:
            raise StorageProviderError(
                "Multiple managed files exist for one internal asset", retryable=False
            )
        return files[0] if files else None

    def _stored(self, input: StoreAssetInput, data: dict) -> StoredAsset:
        return StoredAsset(
            storage_key=f"google-drive:{data['id']}",
            content_hash=input.content_hash,
            size_bytes=int(data["size"]) if data.get("size") else input.size_bytes,
            storage_provider=self.provider_name,
            remote_file_id=data["id"],
            remote_folder_id=(data.get("parents") or [self._root_folder_id])[0],
            web_url=data.get("webViewLink"),
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            raise StorageProviderError(
                f"Google Drive storage request failed with {response.status_code}",
                retryable=True,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise StorageProviderError(str(exc), retryable=False) from exc

    @staticmethod
    def _validate_upload_url(value: str | None) -> None:
        if not value:
            raise StorageProviderError("Google Drive returned no resumable upload URL", retryable=True)
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "googleapis.com" or hostname.endswith(".googleapis.com")
        ):
            raise StorageProviderError("Google Drive returned an unsafe upload URL", retryable=False)
