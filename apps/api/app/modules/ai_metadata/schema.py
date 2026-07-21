from typing import Literal

from pydantic import BaseModel, Field


class EnqueueAssetAnalysisRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=36)
    metadata_profile: str = Field(min_length=1, max_length=255)
    metadata_profile_version: str | None = Field(default=None, max_length=100)
    source_provider: Literal["google-drive", "sharepoint"] = "google-drive"
    ai_provider: Literal["gemini", "openai"] = "gemini"
    processing_mode: Literal["single", "batch"] = "single"
    ai_model: str | None = Field(default=None, min_length=1, max_length=255)
    force: bool = False


class EnqueueAssetAnalysisResponse(BaseModel):
    analysis_id: str
    job_id: str
    provider: Literal["gemini", "openai"]
    model: str
    processing_mode: Literal["single", "batch"]
    status: Literal["accepted"]


class AiModelCapability(BaseModel):
    id: str
    label: str
    supports_single: bool
    supports_batch: bool


class AiProviderCapabilityResponse(BaseModel):
    id: Literal["gemini", "openai"]
    label: str
    enabled: bool
    models: list[AiModelCapability]
    default_model: str
    supported_modes: list[Literal["single", "batch"]]


class AiCapabilitiesResponse(BaseModel):
    providers: list[AiProviderCapabilityResponse]
