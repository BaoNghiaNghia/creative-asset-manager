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
    auto_complete_on_delivery: bool = False
    completion_destination_id: str | None = None
    completed_at: datetime | None = None
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


class GenerationCapabilityResponse(BaseModel):
    enabled: bool
    available: bool
    provider: Literal["gemini"]
    model: str
    operation: Literal["reference_conditioned_product_edit"]
    reason: str | None = None


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
    provider_request_id: str | None = None
    processing_job_id: str | None = None
    status: Literal["prepared", "queued", "running", "completed", "failed"]
    output_content_type: str | None = None
    output_size_bytes: int | None = None
    output_width: int | None = None
    output_height: int | None = None
    output_remote_file_id: str | None = None
    output_web_url: str | None = None
    parent_attempt_id: str | None = None
    correction_supervisor_result_id: str | None = None
    supervisor_correction: dict | None = None
    review_status: Literal["pending", "approved", "rejected"] | None = None
    review_task_id: str | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    export_status: Literal["pending_review", "export_ready", "not_exportable", "exported"] | None = None
    export_record_id: str | None = None
    catalog_asset_id: str | None = None
    exported_by_user_id: str | None = None
    exported_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GenerationAttemptCreatedResponse(BaseModel):
    created: bool
    attempt: GenerationAttemptResponse


class SupervisorResultResponse(BaseModel):
    id: str
    campaign_id: str
    generation_attempt_id: str
    candidate_id: str
    product_id: str
    supervisor_skill_version: str
    status: Literal["queued", "running", "pass", "fail", "needs_human_review", "error"]
    reason: str | None = None
    metrics: dict | None = None
    expected: dict | None = None
    correction: dict | None = None
    summary: str | None = None
    provider: str | None = None
    provider_model: str | None = None
    processing_job_id: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    can_retry: bool = False
    attempt_count: int = 0
    max_attempts: int = 3
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReviewTaskDecisionRequest(BaseModel):
    review_note: str | None = Field(default=None, max_length=1000)


class ReviewTaskResponse(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    candidate_id: str
    product_id: str
    product_sku: str
    product_name: str
    generation_attempt_id: str
    generation_variant: int
    supervisor_result_id: str
    supervisor_status: Literal["pass", "needs_human_review"]
    supervisor_reason: str | None = None
    supervisor_summary: str | None = None
    supervisor_metrics: dict | None = None
    queue_reason: Literal["supervisor_pass", "supervisor_needs_human_review"]
    priority: Literal["standard", "high"]
    status: Literal["pending", "approved", "rejected"]
    review_note: str | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    export_status: Literal["pending_review", "export_ready", "not_exportable", "exported"]
    output_url: str
    created_at: datetime
    updated_at: datetime


class ReviewTaskListResponse(BaseModel):
    items: list[ReviewTaskResponse]
    total: int
    limit: int
    offset: int


class ReviewTaskTransitionResponse(BaseModel):
    transitioned: bool
    task: ReviewTaskResponse


class ReviewTaskReconcileResponse(BaseModel):
    scanned: int
    created: int


class ExportResponse(BaseModel):
    id: str
    campaign_id: str
    generation_attempt_id: str
    review_task_id: str
    catalog_asset_id: str
    content_hash: str
    content_type: str | None = None
    size_bytes: int | None = None
    storage_provider: str
    remote_file_id: str
    remote_folder_id: str | None = None
    web_url: str | None = None
    status: Literal["exported"]
    requested_by_user_id: str
    exported_at: datetime
    created_at: datetime
    updated_at: datetime


class ExportListResponse(BaseModel):
    items: list[ExportResponse]
    total: int
    limit: int
    offset: int


class BatchExportResponse(BaseModel):
    scanned: int
    exported: int
    reused: int
    items: list[ExportResponse]


class CampaignExportSummaryResponse(BaseModel):
    generated: int
    review_pending: int
    approved: int
    rejected: int
    export_ready: int
    exported: int


class DeliveryDestinationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["google_drive_folder"] = "google_drive_folder"
    target_ref: str = Field(min_length=1, max_length=255)
    retention_days: int = Field(default=90, ge=1, le=3650)


class DeliveryDestinationResponse(BaseModel):
    id: str
    name: str
    kind: Literal["google_drive_folder"]
    target_ref: str
    retention_days: int
    active: bool
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class DeliveryItemResponse(BaseModel):
    id: str
    export_id: str
    catalog_asset_id: str
    source_remote_file_id: str
    delivered_remote_file_id: str | None = None
    delivered_web_url: str | None = None
    status: Literal["pending", "delivered", "failed"]
    last_error_code: str | None = None
    last_error_message: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DeliveryPackageResponse(BaseModel):
    id: str
    campaign_id: str
    destination_id: str
    status: Literal["pending", "delivering", "delivered", "partial_failed", "expired"]
    export_count: int
    delivered_count: int
    failed_count: int
    auto_retry_count: int
    items: list[DeliveryItemResponse] = Field(default_factory=list)
    started_at: datetime | None = None
    last_retry_at: datetime | None = None
    next_retry_at: datetime | None = None
    delivered_at: datetime | None = None
    expires_at: datetime | None = None
    expired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DeliveryPackageListResponse(BaseModel):
    items: list[DeliveryPackageResponse]
    total: int
    limit: int
    offset: int


class CampaignLifecyclePolicyRequest(BaseModel):
    auto_complete_on_delivery: bool = False
    completion_destination_id: str | None = Field(default=None, max_length=36)


class CampaignDeliverySummaryResponse(BaseModel):
    campaign_status: CampaignStatus
    auto_complete_on_delivery: bool
    completion_destination_id: str | None = None
    cataloged: int
    packages_total: int
    packages_delivered: int
    packages_partial_failed: int
    packages_expired: int
    latest_delivered_count: int
    latest_export_count: int
    auto_complete_eligible: bool
    completed_at: datetime | None = None


class DeliveryLifecycleReconcileResponse(BaseModel):
    scanned: int
    expired: int


class DeliveryEventResponse(BaseModel):
    id: str
    campaign_id: str
    package_id: str | None = None
    event_type: str
    severity: Literal["info", "warning", "error"]
    message: str
    payload: dict[str, object] | None = None
    created_at: datetime


class DeliveryOperationsSummaryResponse(BaseModel):
    automation_enabled: bool
    campaigns_total: int
    campaigns_completed: int
    destinations_active: int
    packages_total: int
    packages_delivered: int
    packages_partial_failed: int
    packages_expired: int
    retry_due: int
    retry_exhausted: int
    items_delivered: int
    items_failed: int
    latest_delivery_at: datetime | None = None
    maintenance_interval_seconds: int
    auto_retry_max_attempts: int
    recent_events: list[DeliveryEventResponse] = Field(default_factory=list)


class DeliveryMaintenanceEnqueueResponse(BaseModel):
    created: bool
    job_id: str | None = None


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
