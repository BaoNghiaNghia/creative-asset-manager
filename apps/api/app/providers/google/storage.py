from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import PurePath
from urllib.parse import urlsplit

import httpx

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    OpenStoredAssetInput,
    StoredAssetReadStream,
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
    token_uri = "https://oauth2.googleapis.com/token"

    def __init__(
        self,
        storage_access_token: str | None = None,
        *,
        root_folder_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not root_folder_id:
            raise ValueError("managed storage root folder ID is required")
        if not refresh_token and not storage_access_token:
            raise ValueError("managed storage credentials are required")
        if refresh_token and (not client_id or not client_secret):
            raise ValueError(
                "managed storage refresh token requires Google client credentials"
            )
        self._static_access_token = storage_access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._cached_access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._root_folder_id = root_folder_id
        self._transport = transport
        self._clock = clock

    async def open_asset(self, input: OpenStoredAssetInput) -> StoredAssetReadStream:
        access_token = await self._get_access_token()
        client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(60, connect=10, read=60),
            transport=self._transport,
        )
        try:
            request = client.build_request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{input.remote_file_id}",
                params={"alt": "media", "supportsAllDrives": "true"},
            )
            response = await client.send(request, stream=True)
            self._raise_for_status(response)
        except StorageProviderError:
            await client.aclose()
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            await client.aclose()
            raise StorageProviderError(
                "Google Drive managed asset read failed.", retryable=True
            ) from exc

        async def body():
            async for chunk in response.aiter_bytes():
                yield chunk

        closed = False

        async def close() -> None:
            nonlocal closed
            if closed:
                return
            closed = True
            await response.aclose()
            await client.aclose()

        size_header = response.headers.get("content-length")
        return StoredAssetReadStream(
            body=body(),
            close=close,
            content_type=response.headers.get(
                "content-type", input.content_type or "application/octet-stream"
            ),
            size_bytes=(
                int(size_header) if size_header and size_header.isdigit() else input.size_bytes
            ),
        )

    async def store_asset(self, input: StoreAssetInput) -> StoredAsset:
        access_token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
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
        access_token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        content = json.dumps(
            input.metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(60, connect=10, read=60),
            transport=self._transport,
        ) as client:
            existing = await self._find_existing_sidecar(client, input)
            if existing is not None:
                response = await client.patch(
                    f"https://www.googleapis.com/upload/drive/v3/files/{existing['id']}",
                    params={
                        "uploadType": "media",
                        "supportsAllDrives": "true",
                        "fields": "id,parents,webViewLink",
                    },
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    content=content,
                )
                self._raise_for_status(response)
                return self._stored_sidecar(input, {**existing, **response.json()})

            metadata = {
                "name": f"{input.asset_id}.{input.analysis_id}.metadata.json",
                "parents": [self._root_folder_id],
                "mimeType": "application/json",
                "appProperties": {
                    "cam_tenant_id": input.tenant_id,
                    "cam_asset_id": input.asset_id,
                    "cam_analysis_id": input.analysis_id,
                    "cam_sidecar": "metadata-v1",
                },
            }
            create_response = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={
                    "uploadType": "resumable",
                    "supportsAllDrives": "true",
                    "fields": "id,parents,webViewLink",
                },
                headers={
                    "X-Upload-Content-Type": "application/json; charset=utf-8",
                    "X-Upload-Content-Length": str(len(content)),
                },
                json=metadata,
            )
            self._raise_for_status(create_response)
            upload_url = create_response.headers.get("location")
            self._validate_upload_url(upload_url)
            upload_response = await client.put(
                upload_url,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(content)),
                },
                content=content,
            )
            self._raise_for_status(upload_response)
            data = upload_response.json()
            if not data.get("id"):
                raise StorageProviderError(
                    "Google Drive sidecar upload returned no file ID",
                    retryable=True,
                )
            return self._stored_sidecar(input, data)

    async def _get_access_token(self) -> str:
        if not self._refresh_token:
            if not self._static_access_token:
                raise StorageProviderError(
                    "Google Drive managed storage credentials are unavailable.",
                    retryable=False,
                )
            return self._static_access_token

        now = self._clock()
        if (
            self._cached_access_token
            and self._access_token_expires_at > now + 60
        ):
            return self._cached_access_token

        async with self._token_lock:
            now = self._clock()
            if (
                self._cached_access_token
                and self._access_token_expires_at > now + 60
            ):
                return self._cached_access_token
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(20, connect=8),
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        self.token_uri,
                        data={
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                            "refresh_token": self._refresh_token,
                            "grant_type": "refresh_token",
                        },
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise StorageProviderError(
                    "Google managed storage token refresh failed.",
                    retryable=True,
                ) from exc
            if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                raise StorageProviderError(
                    "Google managed storage token refresh failed.",
                    retryable=True,
                )
            if response.status_code >= 400:
                raise StorageProviderError(
                    "Google managed storage credentials were rejected.",
                    retryable=False,
                )
            payload = response.json()
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not isinstance(access_token, str) or not access_token:
                raise StorageProviderError(
                    "Google token response contained no access token.",
                    retryable=True,
                )
            lifetime = float(expires_in) if expires_in is not None else 3600.0
            self._cached_access_token = access_token
            self._access_token_expires_at = self._clock() + max(lifetime, 0.0)
            return access_token

    async def _find_existing_sidecar(
        self,
        client: httpx.AsyncClient,
        input: StoreMetadataSidecarInput,
    ) -> dict | None:
        query = (
            f"'{_escape_query(self._root_folder_id)}' in parents and trashed = false and "
            f"appProperties has {{ key='cam_tenant_id' and value='{_escape_query(input.tenant_id)}' }} and "
            f"appProperties has {{ key='cam_asset_id' and value='{_escape_query(input.asset_id)}' }} and "
            f"appProperties has {{ key='cam_analysis_id' and value='{_escape_query(input.analysis_id)}' }}"
        )
        response = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": query,
                "spaces": "drive",
                "pageSize": "2",
                "fields": "files(id,name,parents,webViewLink,appProperties)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        self._raise_for_status(response)
        files = response.json().get("files") or []
        if len(files) > 1:
            raise StorageProviderError(
                "Multiple metadata sidecars exist for one analysis",
                retryable=False,
            )
        return files[0] if files else None

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

    def _stored_sidecar(
        self,
        input: StoreMetadataSidecarInput,
        data: dict,
    ) -> StoredMetadataSidecar:
        return StoredMetadataSidecar(
            storage_key=f"google-drive:{data['id']}",
            remote_file_id=data["id"],
            remote_folder_id=(data.get("parents") or [self._root_folder_id])[0],
            web_url=data.get("webViewLink"),
            document_hash=input.document_hash,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status_code = response.status_code
        error_by_status = {
            401: ("managed_storage_unauthorized", False),
            403: ("managed_storage_forbidden", False),
            404: ("managed_storage_object_missing", False),
        }
        if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            error_by_status[status_code] = (
                "managed_storage_temporarily_unavailable",
                True,
            )
        mapped = error_by_status.get(status_code)
        if mapped is not None:
            code, retryable = mapped
            raise StorageProviderError(
                f"Google Drive storage request failed with HTTP {status_code}.",
                code=code,
                status_code=status_code,
                retryable=retryable,
                details={"provider": GoogleDriveAssetStorage.provider_name},
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise StorageProviderError(
                f"Google Drive storage request failed with HTTP {status_code}.",
                code="managed_storage_http_error",
                status_code=status_code,
                retryable=False,
                details={"provider": GoogleDriveAssetStorage.provider_name},
            ) from exc

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
