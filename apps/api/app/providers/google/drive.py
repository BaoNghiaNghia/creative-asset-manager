import httpx
from app.providers.google.mapper import map_drive_file

FIELDS = "id,name,mimeType,parents,size,modifiedTime,thumbnailLink,webViewLink"

class GoogleDriveClient:
    def __init__(self, access_token: str):
        self.headers = {"Authorization": f"Bearer {access_token}"}

    async def _get(self, path: str, params: dict):
        async with httpx.AsyncClient(base_url="https://www.googleapis.com/drive/v3", headers=self.headers, timeout=20) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    async def get(self, item_id: str):
        data = await self._get(f"/files/{item_id}", {"fields": FIELDS, "supportsAllDrives": "true"})
        return map_drive_file(data)

    async def children(self, parent_id: str):
        data = await self._get("/files", {
            "q": f"'{parent_id}' in parents and trashed = false", "fields": f"nextPageToken,files({FIELDS})",
            "pageSize": 1000, "orderBy": "folder,name", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        })
        return [map_drive_file(item) for item in data.get("files", [])]
