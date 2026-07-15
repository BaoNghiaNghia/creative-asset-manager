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
            http2=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.client.aclose()

    async def _get(self, path: str, params: dict):
        response = await self.client.get(path, params=params)
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
