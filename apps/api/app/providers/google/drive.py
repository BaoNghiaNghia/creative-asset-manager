import asyncio
import time
from collections import OrderedDict
from threading import Lock

import httpx
from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.schema import AssetNode
from app.providers.google.mapper import map_drive_file

FIELDS = (
    "id,name,mimeType,parents,size,modifiedTime,thumbnailLink,webViewLink,"
    "imageMediaMetadata(width,height,rotation),"
    "videoMediaMetadata(width,height,durationMillis)"
)
FOLDER_MIME = "application/vnd.google-apps.folder"
_MEDIA_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MEDIA_MAX_ATTEMPTS = 3
_THUMBNAIL_LINK_TTL_SECONDS = 30 * 60
_THUMBNAIL_LINK_CACHE_MAX_ENTRIES = 2048
_THUMBNAIL_LINK_REFRESH_STATUS_CODES = frozenset({401, 403, 404})


class ThumbnailLinkCache:
    """Small tenant/source-scoped TTL cache for temporary Drive thumbnail links."""

    def __init__(self, max_entries: int, ttl_seconds: float):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: tuple[str, str, str]) -> str | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[str, str, str], value: str) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, key: tuple[str, str, str]) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


thumbnail_link_cache = ThumbnailLinkCache(
    max_entries=_THUMBNAIL_LINK_CACHE_MAX_ENTRIES,
    ttl_seconds=_THUMBNAIL_LINK_TTL_SECONDS,
)


class GoogleDriveThumbnailUnavailable(Exception):
    """The Drive item has no provider-generated thumbnail."""


class GoogleDriveClient:
    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/drive/v3",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(20, connect=8),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.client.aclose()

    async def _get(self, path: str, params: dict):
        response = None
        for attempt in range(3):
            response = await self.client.get(path, params=params)
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 2:
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2 ** attempt)
                await asyncio.sleep(delay)

        response.raise_for_status()
        return response.json()

    async def upload_file(self, parent_id: str, filename: str, mime_type: str, content: bytes):
        import json
        effective_mime_type = infer_media_type(filename, mime_type)
        response = await self.client.post("https://www.googleapis.com/upload/drive/v3/files", params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": FIELDS}, files={"metadata": (None, json.dumps({"name": filename, "parents": [parent_id]}), "application/json"), "file": (filename, content, effective_mime_type)}); response.raise_for_status(); return map_drive_file(response.json())

    async def create_folder(self, parent_id: str, name: str):
        response = await self.client.post(
            "/files",
            params={"supportsAllDrives": "true", "fields": FIELDS},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        response.raise_for_status()
        return map_drive_file(response.json())

    async def update_file_content(self, item_id: str, filename: str, mime_type: str, content: bytes):
        response = await self.client.patch(
            f"https://www.googleapis.com/upload/drive/v3/files/{item_id}",
            params={"uploadType": "media", "supportsAllDrives": "true", "fields": FIELDS},
            headers={"Content-Type": infer_media_type(filename, mime_type)},
            content=content,
        )
        response.raise_for_status()
        return map_drive_file(response.json())

    async def delete_file(self, item_id: str):
        response = await self.client.delete(f"/files/{item_id}", params={"supportsAllDrives": "true"}); response.raise_for_status()

    async def move_file(self, item_id: str, destination_parent_id: str):
        current = await self.client.get(f"/files/{item_id}", params={"fields": "id,parents", "supportsAllDrives": "true"}); current.raise_for_status(); old=",".join(current.json().get("parents", [])); response=await self.client.patch(f"/files/{item_id}", params={"addParents": destination_parent_id, "removeParents": old, "supportsAllDrives": "true", "fields": FIELDS}); response.raise_for_status(); return map_drive_file(response.json())

    async def ensure_child_folder(self, parent_id: str, name: str):
        """Return an existing direct child folder or create it once."""
        for child in await self.children(parent_id, folders_only=True):
            if child.name == name:
                return child
        response = await self.client.post(
            "/files",
            params={"supportsAllDrives": "true", "fields": FIELDS},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        response.raise_for_status()
        return map_drive_file(response.json())
    async def copy_file(self, item_id: str, destination_parent_id: str):
        source = await self.get(item_id)
        if source.kind == "folder":
            if await self._is_same_or_descendant(destination_parent_id, item_id):
                raise ValueError("A folder cannot be copied into itself or one of its descendants.")
            return await self._copy_folder(item_id, destination_parent_id, source.name)
        response = await self.client.post(
            f"/files/{item_id}/copy",
            params={"supportsAllDrives": "true", "fields": FIELDS},
            json={"name": source.name, "parents": [destination_parent_id]},
        )
        response.raise_for_status()
        return map_drive_file(response.json())

    async def _is_same_or_descendant(self, folder_id: str, possible_ancestor_id: str) -> bool:
        current_id = folder_id
        seen: set[str] = set()
        while current_id not in seen and current_id != "root":
            if current_id == possible_ancestor_id:
                return True
            seen.add(current_id)
            parents = (await self.get(current_id)).parent_id
            if not parents:
                break
            current_id = parents
        return current_id == possible_ancestor_id

    async def _copy_folder(self, source_id: str, destination_parent_id: str, name: str):
        response = await self.client.post(
            "/files",
            params={"supportsAllDrives": "true", "fields": FIELDS},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [destination_parent_id]},
        )
        response.raise_for_status()
        copied = map_drive_file(response.json())
        for child in await self.children(source_id):
            await self.copy_file(child.id, copied.id)
        return copied

    async def get_breadcrumb_metadata(self, item_id: str):
        """Fetch only the provider fields needed to resolve a folder path."""
        data = await self._get(
            f"/files/{item_id}",
            {"fields": "id,name,mimeType,parents,driveId", "supportsAllDrives": "true"},
        )
        return map_drive_file(data)

    async def get_image_dimensions(self, item_id: str):
        data = await self._get(
            f"/files/{item_id}",
            {"fields": "id,mimeType,size,modifiedTime,imageMediaMetadata(width,height,rotation),videoMediaMetadata(width,height,durationMillis)", "supportsAllDrives": "true"},
        )
        metadata = data.get("imageMediaMetadata") or {}
        width, height = metadata.get("width"), metadata.get("height")
        return (int(width), int(height)) if width and height else (None, None)

    async def get(self, item_id: str):
        data = await self._get(
            f"/files/{item_id}",
            {"fields": FIELDS, "supportsAllDrives": "true"},
        )
        return map_drive_file(data)

    async def search_folders(self, value: str, *, limit: int = 50):
        """Search folder names directly in Drive without crawling the tree."""
        normalized = " ".join(value.split())
        if not normalized:
            return []
        escaped = normalized.replace("\\", "\\\\").replace("'", "\\'")
        data = await self._get(
            "/files",
            {
                "q": f"mimeType = '{FOLDER_MIME}' and name contains '{escaped}' and trashed = false",
                "fields": f"nextPageToken,files({FIELDS})",
                "pageSize": max(1, min(limit, 100)),
                "orderBy": "name",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        return [map_drive_file(item) for item in data.get("files", [])[:limit]]

    async def children_page(
        self,
        parent_id: str,
        *,
        folders_only: bool = False,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[AssetNode], str | None]:
        """Return one stable Drive page ordered by folder then name."""
        query = f"'{parent_id}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"
        params = {
            "q": query,
            "fields": f"nextPageToken,files({FIELDS})",
            "pageSize": max(1, min(page_size, 200)),
            "orderBy": "folder,name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        data = await self._get("/files", params)
        return (
            [map_drive_file(item) for item in data.get("files", [])],
            data.get("nextPageToken"),
        )

    async def children(self, parent_id: str, folders_only: bool = False):
        """Iterate every Drive page for background and tree callers."""
        files = []
        page_token = None
        while True:
            page, page_token = await self.children_page(
                parent_id,
                folders_only=folders_only,
                page_token=page_token,
                page_size=200,
            )
            files.extend(page)
            if not page_token:
                return files


def create_stream_client() -> httpx.AsyncClient:
    """Create the bounded, application-owned client used for Drive streaming."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(20, read=None),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=True,
    )


async def open_media_stream(
    access_token: str,
    item_id: str,
    range_header: str | None,
    *,
    http_client: httpx.AsyncClient | None = None,
):
    """Open an authenticated Drive media stream without buffering it in the API.

    Transient Google errors and redirects are resolved before the response is
    handed to StreamingResponse, which keeps the actual media body unbuffered.
    """
    client = http_client or create_stream_client()
    owns_client = http_client is None
    headers = {"Authorization": f"Bearer {access_token}"}
    if range_header:
        headers["Range"] = range_header

    response = None
    try:
        for attempt in range(_MEDIA_MAX_ATTEMPTS):
            request = client.build_request(
                "GET",
                "https://www.googleapis.com/drive/v3/files/{item_id}".format(
                    item_id=item_id
                ),
                params={"alt": "media", "supportsAllDrives": "true"},
                headers=headers,
            )
            response = await client.send(request, stream=True)
            if (
                response.status_code not in _MEDIA_RETRYABLE_STATUS_CODES
                or attempt == _MEDIA_MAX_ATTEMPTS - 1
            ):
                response.raise_for_status()
                return client, response

            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after is not None else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            await response.aclose()
            response = None
            await asyncio.sleep(max(delay, 0.0))
    except Exception:
        if response is not None:
            await response.aclose()
        if owns_client:
            await client.aclose()
        raise


async def close_media_stream(
    client: httpx.AsyncClient,
    response: httpx.Response,
    close_client: bool = True,
):
    await response.aclose()
    if close_client:
        await client.aclose()


async def open_thumbnail_stream(
    access_token: str,
    item_id: str,
    *,
    cache_key: tuple[str, str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
):
    """Resolve and stream a Drive thumbnail without repeatedly fetching metadata."""
    client = http_client or create_stream_client()
    owns_client = http_client is None
    response = None
    try:
        thumbnail_url = thumbnail_link_cache.get(cache_key) if cache_key else None
        used_cached_url = thumbnail_url is not None
        for attempt in range(2):
            if thumbnail_url is None:
                metadata = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{item_id}",
                    params={"fields": "thumbnailLink", "supportsAllDrives": "true"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                metadata.raise_for_status()
                thumbnail_url = str(metadata.json().get("thumbnailLink") or "").strip()
                if not thumbnail_url:
                    raise GoogleDriveThumbnailUnavailable(item_id)
                if cache_key:
                    thumbnail_link_cache.put(cache_key, thumbnail_url)

            request = client.build_request(
                "GET",
                thumbnail_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response = await client.send(request, stream=True)
            if (
                attempt == 0
                and used_cached_url
                and response.status_code in _THUMBNAIL_LINK_REFRESH_STATUS_CODES
            ):
                await response.aclose()
                response = None
                if cache_key:
                    thumbnail_link_cache.invalidate(cache_key)
                thumbnail_url = None
                used_cached_url = False
                continue
            response.raise_for_status()
            return client, response
        raise GoogleDriveThumbnailUnavailable(item_id)
    except Exception:
        if response is not None:
            await response.aclose()
        if owns_client:
            await client.aclose()
        raise


async def close_thumbnail_stream(
    client: httpx.AsyncClient,
    response: httpx.Response,
    close_client: bool = True,
):
    await response.aclose()
    if close_client:
        await client.aclose()
