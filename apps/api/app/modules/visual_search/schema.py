from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.modules.search.schema import SearchCoreFilters

MIN_NORMALIZED_CROP_EDGE = 0.01


class NormalizedCrop(BaseModel):
    """Crop coordinates expressed against EXIF-normalized image dimensions."""

    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(ge=MIN_NORMALIZED_CROP_EDGE, le=1)
    height: float = Field(ge=MIN_NORMALIZED_CROP_EDGE, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "NormalizedCrop":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("crop must remain within normalized image bounds")
        return self


class VisualSearchByAssetRequest(BaseModel):
    """Public request contract for a later tenant-authorized by-asset endpoint."""

    asset_id: str = Field(min_length=1, max_length=36)
    crop: NormalizedCrop | None = None
    text: str | None = Field(default=None, max_length=500)
    filters: SearchCoreFilters = Field(default_factory=SearchCoreFilters)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=40, ge=1, le=100)

    @model_validator(mode="after")
    def normalize_text(self) -> "VisualSearchByAssetRequest":
        self.text = (self.text or "").strip() or None
        return self


class VisualSearchResponse(BaseModel):
    """Result envelope. Items use the existing hydrated asset-card DTO shape."""

    query_kind: Literal["asset", "upload"]
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool = False
