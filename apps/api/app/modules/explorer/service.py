import os
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
    async def list_folder(self, parent_id: str) -> FolderListing:
        token = os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN")
        if token:
            client = GoogleDriveClient(token)
            parent, children = await client.get(parent_id), await client.children(parent_id)
        else:
            parent = AssetNode(id="root", name="My Drive", kind="folder", mime_type=FOLDER, has_children=True) if parent_id == "root" else next(x for x in MOCK if x.id == parent_id)
            children = [x for x in MOCK if x.parent_id == parent_id]
        return FolderListing(parent=parent, children=children)
