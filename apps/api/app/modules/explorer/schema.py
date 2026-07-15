from datetime import datetime
from typing import Literal
from pydantic import BaseModel

AssetKind = Literal["folder", "image", "video", "pdf", "document", "other"]

class AssetNode(BaseModel):
    id: str
    provider: Literal["google-drive"] = "google-drive"
    name: str
    kind: AssetKind
    mime_type: str
    parent_id: str | None = None
    size: int | None = None
    modified_at: datetime | None = None
    thumbnail_url: str | None = None
    web_url: str | None = None
    has_children: bool = False

class FolderListing(BaseModel):
    parent: AssetNode
    children: list[AssetNode]
