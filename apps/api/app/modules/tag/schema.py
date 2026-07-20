from typing import Literal

from pydantic import BaseModel, Field

from app.modules.explorer.schema import Provider


class Tag(BaseModel):
    id: str
    name: str
    color: str = "#3584e4"
    group_key: str | None = None
    is_system: bool = False


class AssignTagsRequest(BaseModel):
    provider: Provider = "google-drive"
    item_ids: list[str] = Field(min_length=1)
    tag_id: str


class AssetMetadata(BaseModel):
    item_id: str
    tag_ids: list[str] = Field(default_factory=list)
    rating: int | None = Field(default=None, ge=1, le=5)
    processing_status: Literal[
        "discovered",
        "stored",
        "analyzing",
        "metadata_ready",
        "indexed",
        "duplicate",
        "failed",
    ] = "discovered"


class MetadataResponse(BaseModel):
    items: list[AssetMetadata]
