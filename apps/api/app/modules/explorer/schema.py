from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    ancestor_ids: list[str] = Field(default_factory=list)
    ancestor_names: list[str] = Field(default_factory=list)


class FolderListing(BaseModel):
    parent: AssetNode
    children: list[AssetNode]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    root_id: str = "root"
    ancestor_ids: list[str] = Field(default_factory=list)
    ancestor_names: list[str] = Field(default_factory=list)
    limit: int = Field(default=200, ge=1, le=500)


class SearchResponse(BaseModel):
    items: list[AssetNode]
    indexed_count: int
    index_source: Literal["directus", "memory"]
    truncated: bool = False
