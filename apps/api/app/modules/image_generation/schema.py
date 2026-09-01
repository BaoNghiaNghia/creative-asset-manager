from typing import Literal
from pydantic import BaseModel, Field

class SquareGenerationRequest(BaseModel):
    source_asset_id: str = Field(min_length=1, max_length=36)
    provider: Literal["adobe_firefly", "gemini"]
    target_size: Literal[1024, 2048]
    prompt: str | None = Field(default=None, max_length=2000)
    client_request_id: str = Field(min_length=36, max_length=36)

class ProviderCapability(BaseModel):
    id: Literal["adobe_firefly", "gemini"]
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
