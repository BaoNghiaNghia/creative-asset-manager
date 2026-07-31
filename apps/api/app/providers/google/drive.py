import asyncio

import httpx
from app.providers.google.mapper import map_drive_file

FIELDS = "id,name,mimeType,parents,size,modifiedTime,thumbnailLink,webViewLink"
FOLDER_MIME = "application/vnd.google-apps.folder"


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
        response = await self.client.post("https://www.googleapis.com/upload/drive/v3/files", params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": FIELDS}, files={"metadata": (None, json.dumps({"name": filename, "parents": [parent_id]}), "application/json"), "file": (filename, content, mime_type or "application/octet-stream")}); response.raise_for_status(); return map_drive_file(response.json())

    async def delete_file(self, item_id: str):
        response = await self.client.delete(f"/files/{item_id}", params={"supportsAllDrives": "true"}); response.raise_for_status()

    async def move_file(self, item_id: str, destination_parent_id: str):
        current = await self.client.get(f"/files/{item_id}", params={"fields": "id,parents", "supportsAllDrives": "true"}); current.raise_for_status(); old=",".join(current.json().get("parents", [])); response=await self.client.patch(f"/files/{item_id}", params={"addParents": destination_parent_id, "removeParents": old, "supportsAllDrives": "true", "fields": FIELDS}); response.raise_for_status(); return map_drive_file(response.json())

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

    async def get(self, item_id: str):
        data = await self._get(
            f"/files/{item_id}",
            {"fields": FIELDS, "supportsAllDrives": "true"},
        )
        return map_drive_file(data)

    async def children(self, parent_id: str, folders_only: bool = False):
        files = []
        page_token = None
        query = f"'{parent_id}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"

        while True:
            params = {
                "q": query,
                "fields": f"nextPageToken,files({FIELDS})",
                "pageSize": 1000,
                "orderBy": "folder,name",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._get("/files", params)
            files.extend(map_drive_file(item) for item in data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return files


async def open_media_stream(access_token: str, item_id: str, range_header: str | None):
    """Open an authenticated Drive media stream without buffering it in the API."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(20, read=None))
    headers = {"Authorization": f"Bearer {access_token}"}
    if range_header:
        headers["Range"] = range_header

    request = client.build_request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{item_id}",
        params={"alt": "media", "supportsAllDrives": "true"},
        headers=headers,
    )
    response = await client.send(request, stream=True)
    try:
        response.raise_for_status()
    except Exception:
        await response.aclose()
        await client.aclose()
        raise
    return client, response


async def close_media_stream(client: httpx.AsyncClient, response: httpx.Response):
    await response.aclose()
    await client.aclose()
