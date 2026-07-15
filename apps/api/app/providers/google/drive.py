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
