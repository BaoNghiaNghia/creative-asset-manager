import asyncio

from app.modules.explorer.schema import AssetNode, FolderListing
from app.providers.google.drive import GoogleDriveClient

FOLDER = "application/vnd.google-apps.folder"
MOCK = [
    AssetNode(id="campaigns", name="Campaigns", kind="folder", mime_type=FOLDER, parent_id="root", has_children=True),
    AssetNode(id="brand", name="Brand library", kind="folder", mime_type=FOLDER, parent_id="root", has_children=True),
    AssetNode(id="hero", name="hero-banner.jpg", kind="image", mime_type="image/jpeg", parent_id="campaigns", size=4820000),
    AssetNode(id="film", name="launch-film.mp4", kind="video", mime_type="video/mp4", parent_id="campaigns", size=128420000),
]


class ExplorerService:
    async def list_folder(self, parent_id: str, access_token: str | None) -> FolderListing:
        if access_token:
            async with GoogleDriveClient(access_token) as client:
                parent, children = await asyncio.gather(
                    client.get(parent_id),
                    client.children(parent_id),
                )
        else:
            parent = (
                AssetNode(id="root", name="My Drive", kind="folder", mime_type=FOLDER, has_children=True)
                if parent_id == "root"
                else next(item for item in MOCK if item.id == parent_id)
            )
            children = [item for item in MOCK if item.parent_id == parent_id]
        return FolderListing(parent=parent, children=children)

    async def list_folders(self, parent_id: str, access_token: str | None) -> list[AssetNode]:
        if access_token:
            async with GoogleDriveClient(access_token) as client:
                return await client.children(parent_id, folders_only=True)
        return [item for item in MOCK if item.parent_id == parent_id and item.kind == "folder"]
