from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

GenerationStatus = Literal[
    "queued", "preparing", "submitted", "running", "storing",
    "completed", "failed", "cancelled",
]


class SquareGenerationRequest(BaseModel):
    source_asset_id: str = Field(min_length=1, max_length=36)
    source_source_asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    provider: Literal["adobe_firefly", "cloudflare_sd", "gemini"]
    target_size: Literal[1024, 2048]
    prompt: str | None = Field(default=None, max_length=2000)
    client_request_id: str = Field(min_length=36, max_length=36)


class GenerationError(BaseModel):
    code: str
    message: str


class ImageGenerationResponse(BaseModel):
    id: str
    source_asset_id: str
    status: GenerationStatus
    provider: Literal["adobe_firefly", "cloudflare_sd", "gemini"]
    model: str | None
    preservation_mode: Literal["strict_expand", "semantic_expand"]
    target_width: int
    target_height: int
    output_asset_id: str | None
    error: GenerationError | None
    created_at: datetime
    completed_at: datetime | None


class ProviderCapability(BaseModel):
    id: Literal["adobe_firefly", "cloudflare_sd", "gemini"]
    name: str
    available: bool
    preservation_mode: Literal["strict_expand", "semantic_expand"]
    recommended: bool
    model: str | None = None


class ImageGenerationCapability(BaseModel):
    enabled: bool
    operations: list[Literal["square_expand"]]
    target_sizes: list[Literal[1024, 2048]]
    providers: list[ProviderCapability]
