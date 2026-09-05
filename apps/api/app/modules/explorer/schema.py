from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AssetKind = Literal["folder", "image", "video", "pdf", "document", "other"]
Provider = Literal["google-drive", "onedrive", "sharepoint"]


class LocationBreadcrumbNode(BaseModel):
    id: str
    name: str


class AssetLocationResponse(BaseModel):
    status: Literal["available", "unavailable"]
    breadcrumb: list[LocationBreadcrumbNode] = Field(default_factory=list)


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
    location_breadcrumb: list[LocationBreadcrumbNode] = Field(default_factory=list)
    location_unavailable: bool = False
    location_status: Literal["resolved", "unavailable"] = "unavailable"
    image_width: int | None = None
    image_height: int | None = None
    media_duration_ms: int | None = None


class FolderListing(BaseModel):
    parent: AssetNode
    children: list[AssetNode]
    next_page_token: str | None = None
    has_more: bool = False


class ViewerBootstrapFolder(BaseModel):
    id: str
    name: str
    external_source_id: str


class ViewerBootstrapSource(BaseModel):
    external_source_id: str
    display_name: str
    folders: list[ViewerBootstrapFolder]


class ViewerBootstrapResponse(BaseModel):
    sources: list[ViewerBootstrapSource]
    auto_selected_source_id: str | None = None
    auto_selected_folder_id: str | None = None


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


class FolderNoteResponse(BaseModel):
    requested_folder_id: str
    note_owner_folder_id: str | None = None
    note_owner_folder_name: str | None = None
    is_inherited: bool = False
    content_markdown: str = ""
    updated_at: datetime | None = None
    updated_by: str | None = None


class FolderNoteUpdateRequest(BaseModel):
    content_markdown: str = Field(default="", max_length=50_000)


class ContentHashPreflightRequest(BaseModel):
    hashes: list[str] = Field(min_length=1, max_length=500)


class ContentHashPreflightResponse(BaseModel):
    existing: dict[str, bool]
