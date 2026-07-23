from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AssetKind = Literal["folder", "image", "video", "pdf", "document", "other"]
Provider = Literal["google-drive", "sharepoint"]


class AssetNode(BaseModel):
    id: str
    internal_asset_id: str | None = None
    source_asset_id: str | None = None
    external_source_id: str | None = None
    provider: Provider = "google-drive"
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
    provider: Provider = "google-drive"
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
    skipped_folders: int = 0


class IndexRequest(BaseModel):
    provider: Provider = "google-drive"
    root_id: str = "root"
    ancestor_ids: list[str] = Field(default_factory=list)
    ancestor_names: list[str] = Field(default_factory=list)


class IndexStatus(BaseModel):
    state: Literal["idle", "running", "completed", "failed"] = "idle"
    status: str = "Waiting to index Google Drive"
    progress: int = Field(default=0, ge=0, le=100)
    indexed_count: int = 0
    processed_folders: int = 0
    pending_folders: int = 0
    skipped_folders: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
