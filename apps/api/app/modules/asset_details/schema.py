from typing import Any, Literal
from pydantic import BaseModel, Field

class AssetActionRequest(BaseModel):
    action: Literal["reanalyze", "rebuild_projection", "reindex", "retry_failed_stage", "cancel_job"]
    force: bool = False
    confirmed: bool = False
    job_id: str | None = None

class AcceptedAssetAction(BaseModel):
    action: str
    status: Literal["accepted", "cancelled"]
    job_id: str
    analysis_id: str | None = None

class AssetDetailsResponse(BaseModel):
    asset: dict[str, Any]
    sources: list[dict[str, Any]]
    storage: list[dict[str, Any]]
    active_analysis: dict[str, Any] | None
    analysis_history: list[dict[str, Any]]
    analysis_total: int
    jobs: list[dict[str, Any]]
    job_total: int
    pipelines: list[dict[str, Any]]
    lifecycle_status: str
    can_administer: bool
    limits: dict[str, int] = Field(default_factory=dict)
