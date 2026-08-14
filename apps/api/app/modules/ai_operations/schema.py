from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class AiOperationsFilters:
    tenant_id: str
    from_at: datetime
    to_at: datetime
    provider: str | None = None
    model: str | None = None
    processing_mode: str | None = None
    metadata_profile: str | None = None
    status: str | None = None
    source_provider: str | None = None


AI_JOB_TYPES = (
    "asset_analyze",
    "ai_batch_prepare",
    "ai_batch_submit",
    "ai_batch_poll",
    "ai_batch_import",
    "ai_batch_retry_items",
)


class SearchCoverageAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verify_elasticsearch: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class SearchCoverageRepairRequest(SearchCoverageAuditRequest):
    confirmed: bool
    repair_projections: bool = True
    repair_indexes: bool = True


class ManagedStorageCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    limit: int = Field(default=100, ge=1, le=500)
