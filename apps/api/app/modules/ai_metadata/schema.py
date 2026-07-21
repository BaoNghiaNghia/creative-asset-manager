from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BULK_ANALYSIS_ITEMS = 1_000


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


BulkAcceptanceStatus = Literal[
    "accepted",
    "already_exists",
    "invalid_asset",
    "unauthorized",
    "provider_unavailable",
    "budget_preflight_failed",
]


class BulkAssetAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_BULK_ANALYSIS_ITEMS,
    )
    metadata_profile: str = Field(min_length=1, max_length=255)
    metadata_profile_version: str | None = Field(default=None, max_length=100)
    ai_provider: Literal["gemini", "openai"] = "gemini"
    processing_mode: Literal["single", "batch"] = "single"
    ai_model: str | None = Field(default=None, min_length=1, max_length=255)
    force: bool = False

    @model_validator(mode="after")
    def validate_unique_assets(self) -> "BulkAssetAnalysisRequest":
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("asset_ids must be unique")
        return self


class BulkAssetAnalysisItemResponse(BaseModel):
    asset_id: str
    acceptance_status: BulkAcceptanceStatus
    analysis_id: str | None
    job_id: str | None
    error_code: str | None
    error_message: str | None


class BulkAssetAnalysisAcceptedResponse(BaseModel):
    request_id: str
    status: Literal["accepted"]
    provider: Literal["gemini", "openai"]
    model: str
    processing_mode: Literal["single", "batch"]
    analysis_count: int
    warning: str | None
    items: list[BulkAssetAnalysisItemResponse]


class AnalysisRequestItemStatusResponse(BulkAssetAnalysisItemResponse):
    processing_status: str
    batch_id: str | None
    provider_batch_id: str | None = None


class AnalysisRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    provider: Literal["gemini", "openai"]
    model: str
    processing_mode: Literal["single", "batch"]
    analysis_count: int
    batch_count: int
    queued: int
    running: int
    completed: int
    failed: int
    cancelled: int
    warning: str | None
    items: list[AnalysisRequestItemStatusResponse]
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None


class CancelAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1_000)
