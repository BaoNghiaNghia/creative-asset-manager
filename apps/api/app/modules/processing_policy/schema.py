from typing import Literal

from pydantic import BaseModel, Field


class TenantPolicyPatch(BaseModel):
    pipeline_enabled: bool | None = None
    source_sync_enabled: bool | None = None
    download_enabled: bool | None = None
    managed_storage_enabled: bool | None = None
    ai_analysis_enabled: bool | None = None
    search_v2_enabled: bool | None = None
    sidecar_enabled: bool | None = None
    rollout_mode: Literal["explicit", "percentage"] | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    total_active_jobs_limit: int | None = Field(default=None, ge=1, le=1000)
    ai_active_jobs_limit: int | None = Field(default=None, ge=1, le=1000)
    source_active_jobs_limit: int | None = Field(default=None, ge=1, le=1000)
    storage_active_jobs_limit: int | None = Field(default=None, ge=1, le=1000)
    reason: str | None = Field(default=None, max_length=2000)


class PauseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    graceful_drain: bool = True


class ResumeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ProviderPolicyPatch(BaseModel):
    processing_enabled: bool | None = None
    active_jobs_limit: int | None = Field(default=None, ge=1, le=1000)
    reason: str | None = Field(default=None, max_length=2000)
