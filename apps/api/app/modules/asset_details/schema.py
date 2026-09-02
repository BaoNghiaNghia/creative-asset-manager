from typing import Any, Literal
from pydantic import BaseModel, Field
from app.modules.explorer.schema import LocationBreadcrumbNode

class AssetActionRequest(BaseModel):
    action: Literal["reanalyze", "rebuild_projection", "reindex", "retry_failed_stage", "cancel_job"]
    force: bool = False
    confirmed: bool = False
    job_id: str | None = None
    reason: str | None = Field(None, max_length=1000)

class AcceptedAssetAction(BaseModel):
    action: str
    status: Literal["accepted", "cancelled"]
    job_id: str
    analysis_id: str | None = None

class AssetDetailsResponse(BaseModel):
    asset: dict[str, Any]
    sources: list[dict[str, Any]]
    location_breadcrumb: list[LocationBreadcrumbNode] = Field(default_factory=list)
    location_unavailable: bool = False
    location_status: Literal["resolved", "unavailable"] = "unavailable"
    image_width: int | None = None
    image_height: int | None = None
    resolution_source: Literal["database", "drive_metadata", "media_header", "provider_forbidden", "provider_missing", "media_header_failed", "unavailable"] = "unavailable"
    resolution_status: Literal["available", "unavailable"] = "unavailable"
    storage: list[dict[str, Any]]
    active_analysis: dict[str, Any] | None
    analysis_history: list[dict[str, Any]]
    analysis_total: int
    jobs: list[dict[str, Any]]
    job_total: int
    pipelines: list[dict[str, Any]]
    lifecycle_status: str
    can_administer: bool
    can_generate: bool = False
    can_manage_content: bool = False
    limits: dict[str, int] = Field(default_factory=dict)
