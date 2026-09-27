from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


CampaignStatus = Literal["running", "paused", "completed", "stopped"]
ScoutStatus = Literal["offline", "ready", "busy", "needs_login", "error"]
ProductStatus = Literal["active", "archived"]
ProductReferenceStatus = Literal["active", "archived"]
ProductReferenceView = Literal[
    "front",
    "front_45_left",
    "front_45_right",
    "side_left",
    "side_right",
    "back",
    "top",
    "logo_closeup",
    "embroidery_closeup",
    "material_closeup",
]
CandidateStatus = Literal[
    "discovered",
    "analysis_queued",
    "analyzing",
    "approved",
    "analysis_failed",
    "rejected_no_person",
    "rejected_head_ratio",
    "rejected_expression",
    "rejected_existing_headwear",
    "rejected_head_occlusion",
    "rejected_quality",
    "rejected_ai_risk",
    "rejected_context",
    "import_queued",
    "importing",
    "drive_ready",
    "import_failed",
    "rejected_duplicate",
]


class ProductCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)
    product_type: str = Field(default="hat", min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=120)
    material: str | None = Field(default=None, max_length=200)
    crown_profile: str | None = Field(default=None, max_length=64)
    crown_height_mm: float | None = Field(default=None, gt=0, le=500)
    brim_style: str | None = Field(default=None, max_length=64)
    brim_length_mm: float | None = Field(default=None, gt=0, le=500)
    circumference_mm: float | None = Field(default=None, gt=0, le=2000)
    logo_position: str | None = Field(default=None, max_length=120)
    fit_notes: str | None = Field(default=None, max_length=4000)


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    product_type: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=120)
    material: str | None = Field(default=None, max_length=200)
    crown_profile: str | None = Field(default=None, max_length=64)
    crown_height_mm: float | None = Field(default=None, gt=0, le=500)
    brim_style: str | None = Field(default=None, max_length=64)
    brim_length_mm: float | None = Field(default=None, gt=0, le=500)
    circumference_mm: float | None = Field(default=None, gt=0, le=2000)
    logo_position: str | None = Field(default=None, max_length=120)
    fit_notes: str | None = Field(default=None, max_length=4000)


class ProductResponse(BaseModel):
    id: str
    sku: str
    name: str
    product_type: str
    color: str | None
    material: str | None
    crown_profile: str | None
    crown_height_mm: float | None
    brim_style: str | None
    brim_length_mm: float | None
    circumference_mm: float | None
    logo_position: str | None
    fit_notes: str | None
    revision: int
    status: ProductStatus
    reference_count: int = 0
    active_views: list[ProductReferenceView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ProductReferenceResponse(BaseModel):
    id: str
    product_id: str
    view_type: ProductReferenceView
    version: int
    status: ProductReferenceStatus
    content_hash: str
    original_filename: str | None
    content_type: str
    size_bytes: int
    width: int
    height: int
    image_format: str
    remote_file_id: str | None
    remote_folder_id: str | None
    web_url: str | None
    reused_storage: bool
    created_at: datetime
    archived_at: datetime | None


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=500)
    target_count: int = Field(default=100, ge=1, le=5000)
    max_scroll_batches: int = Field(default=5, ge=1, le=50)
    auto_import: bool = False
    min_head_ratio: float = Field(default=0.20, ge=0.05, le=0.90)
    max_head_ratio: float = Field(default=0.45, ge=0.05, le=0.95)
    min_smile_score: float = Field(default=0.65, ge=0.0, le=1.0)
    max_head_occlusion: float = Field(default=0.25, ge=0.0, le=1.0)
    max_ai_risk_score: float = Field(default=0.20, ge=0.0, le=1.0)
    min_quality_score: float = Field(default=0.55, ge=0.0, le=1.0)
    min_ugc_score: float = Field(default=0.55, ge=0.0, le=1.0)
    min_product_fit_score: float = Field(default=0.55, ge=0.0, le=1.0)
    require_head_visible: bool = True
    reject_headwear: bool = True

    @model_validator(mode="after")
    def validate_head_ratio_range(self):
        if self.min_head_ratio >= self.max_head_ratio:
            raise ValueError("min_head_ratio must be lower than max_head_ratio")
        return self


class CampaignResponse(BaseModel):
    id: str
    name: str
    query: str
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    min_head_ratio: float
    max_head_ratio: float
    min_smile_score: float
    max_head_occlusion: float
    max_ai_risk_score: float
    min_quality_score: float
    min_ugc_score: float
    min_product_fit_score: float
    require_head_visible: bool
    reject_headwear: bool
    product_id: str | None = None
    product_sku: str | None = None
    product_name: str | None = None
    product_revision: int | None = None
    product_reference_count: int = 0
    product_reference_views: list[ProductReferenceView] = Field(default_factory=list)
    product_bound_at: datetime | None = None
    product_binding_stale: bool = False
    generation_ready: bool = False
    status: CampaignStatus
    scout_status: ScoutStatus
    scout_last_seen_at: datetime | None
    discovered: int = 0
    analysis_pending: int = 0
    analyzing: int = 0
    approved: int = 0
    rejected: int = 0
    drive_ready: int = 0
    failed: int = 0
    created_at: datetime
    updated_at: datetime


class CampaignCreatedResponse(CampaignResponse):
    scout_token: str


class CampaignProductBindRequest(BaseModel):
    product_id: str | None = Field(default=None, max_length=36)


class GenerationAttemptCreateRequest(BaseModel):
    generation_variant: int = Field(default=1, ge=1, le=20)
    worker_skill_version: str = Field(
        default="worker-hat-v1", min_length=1, max_length=128
    )


class GenerationAttemptResponse(BaseModel):
    id: str
    campaign_id: str
    candidate_id: str
    product_id: str
    product_revision: int
    product_sku: str
    product_name: str
    reference_count: int
    reference_views: list[ProductReferenceView]
    generation_variant: int
    worker_skill_version: str
    provider: str | None
    provider_model: str | None
    status: Literal["prepared"]
    created_at: datetime
    updated_at: datetime


class GenerationAttemptCreatedResponse(BaseModel):
    created: bool
    attempt: GenerationAttemptResponse


class CandidateSubmission(BaseModel):
    pin_url: str = Field(min_length=1, max_length=2048)
    image_url: str = Field(min_length=1, max_length=4096)
    alt_text: str | None = Field(default=None, max_length=2000)


class CandidateBatchRequest(BaseModel):
    items: list[CandidateSubmission] = Field(min_length=1, max_length=50)


class CandidateResponse(BaseModel):
    id: str
    campaign_id: str
    pin_url: str
    image_url: str
    alt_text: str | None
    status: CandidateStatus
    analysis_revision: int
    import_revision: int
    people_count: int | None
    primary_head_ratio: float | None
    smile_score: float | None
    head_visible: bool | None
    existing_headwear: bool | None
    head_occlusion: float | None
    mobile_ugc_score: float | None
    quality_score: float | None
    ai_risk_score: float | None
    product_fit_score: float | None
    final_score: float | None
    reject_reason: str | None
    analyzer_provider: str | None
    analyzer_model: str | None
    analyzer_version: str | None
    analysis_summary: str | None
    analyzed_at: datetime | None
    content_hash: str | None
    width: int | None
    height: int | None
    size_bytes: int | None
    remote_file_id: str | None
    remote_folder_id: str | None
    web_url: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class CandidateBatchResponse(BaseModel):
    created: int
    existing: int
    items: list[CandidateResponse]


class ScoutHeartbeatRequest(BaseModel):
    status: ScoutStatus


class ScoutTaskResponse(BaseModel):
    campaign_id: str
    query: str
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    status: CampaignStatus
    discovered: int
    approved: int
    rejected: int
    analysis_pending: int
    drive_ready: int


class ImportResponse(BaseModel):
    candidate: CandidateResponse


class AnalyzeResponse(BaseModel):
    candidate: CandidateResponse
