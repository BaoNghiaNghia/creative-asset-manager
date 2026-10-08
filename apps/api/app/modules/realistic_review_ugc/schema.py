from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CampaignStatus = Literal["running", "paused", "completed", "stopped"]
DiscoveryMode = Literal["keyword", "product_context"]
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
AiManualLabel = Literal["real", "ai", "unsure"]
ReferenceManualLabel = Literal["good", "bad", "ai"]
ReferenceSeedLabel = Literal["positive", "negative"]
CandidateStatus = Literal[
    "discovered",
    "analysis_queued",
    "analyzing",
    "approved",
    "needs_review",
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


def normalize_search_queries(values: list[str] | None, fallback: str | None = None) -> list[str]:
    rows = [fallback] if fallback else []
    rows.extend(values or [])
    result: list[str] = []
    seen: set[str] = set()
    for raw in rows:
        value = str(raw or "").strip()
        if not value:
            continue
        if len(value) > 500:
            raise ValueError("Each search query must be 500 characters or fewer")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    if not result:
        raise ValueError("At least one search query is required")
    if len(result) > 10:
        raise ValueError("A campaign can contain at most 10 search queries")
    return result


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


class ProductVariantResponse(BaseModel):
    id: str
    product_id: str
    source_variant_id: str
    sku: str | None = None
    name: str | None = None
    color: str | None = None
    size: str | None = None
    price_text: str | None = None
    currency: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    available: bool = True
    enabled: bool = True
    status: Literal["active", "archived"]
    position: int = 0
    reference_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProductVariantUpdateRequest(BaseModel):
    enabled: bool


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
    source_url: str | None = None
    source_host: str | None = None
    brand: str | None = None
    source_description: str | None = None
    source_category: str | None = None
    source_price_text: str | None = None
    source_currency: str | None = None
    source_images: list[str] = Field(default_factory=list)
    source_variants: list[dict] = Field(default_factory=list)
    variants: list[ProductVariantResponse] = Field(default_factory=list)
    source_metadata: dict = Field(default_factory=dict)
    source_fetched_at: datetime | None = None
    revision: int
    status: ProductStatus
    reference_count: int = 0
    active_views: list[ProductReferenceView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ProductUrlImportRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    import_primary_image: bool = True

    @model_validator(mode="after")
    def validate_urls(self):
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in self.urls:
            value = str(raw or "").strip()
            if not value:
                continue
            if len(value) > 2048:
                raise ValueError("Each product URL must be 2048 characters or fewer")
            if value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        if not cleaned:
            raise ValueError("At least one product URL is required")
        self.urls = cleaned
        return self


class ProductUrlImportItemResponse(BaseModel):
    source_url: str
    status: Literal["created", "updated", "failed"]
    product: ProductResponse | None = None
    images_found: int = 0
    variants_found: int = 0
    variant_references_imported: int = 0
    primary_reference_imported: bool = False
    warning: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ProductUrlImportResponse(BaseModel):
    items: list[ProductUrlImportItemResponse]
    created: int
    updated: int
    failed: int


class ProductReferenceResponse(BaseModel):
    id: str
    product_id: str
    variant_id: str | None = None
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


class ProductContextInput(BaseModel):
    auto_context: bool = True
    themes: list[str] = Field(default_factory=list, max_length=8)
    preferred_scenes: list[str] = Field(default_factory=list, max_length=8)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def normalize_values(self):
        def clean(values: list[str]) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            for raw in values:
                value = str(raw or "").strip()
                key = value.casefold()
                if not value or key in seen:
                    continue
                seen.add(key)
                result.append(value[:160])
            return result

        self.themes = clean(self.themes)
        self.preferred_scenes = clean(self.preferred_scenes)
        self.avoid = clean(self.avoid)
        self.notes = (self.notes or "").strip() or None
        return self


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=500)
    search_queries: list[str] = Field(default_factory=list)
    discovery_mode: DiscoveryMode = "keyword"
    product_context: ProductContextInput | None = None
    target_count: int = Field(default=100, ge=1, le=5000)
    max_scroll_batches: int = Field(default=5, ge=1, le=50)
    auto_import: bool = False
    auto_scout: bool = True
    scan_interval_seconds: int = Field(default=300, ge=60, le=86400)
    min_head_ratio: float = Field(default=0.18, ge=0.05, le=0.90)
    max_head_ratio: float = Field(default=0.70, ge=0.05, le=0.95)
    min_smile_score: float = Field(default=0.00, ge=0.0, le=1.0)
    max_head_occlusion: float = Field(default=0.65, ge=0.0, le=1.0)
    max_ai_risk_score: float = Field(default=0.15, ge=0.0, le=1.0)
    min_quality_score: float = Field(default=0.60, ge=0.0, le=1.0)
    min_ugc_score: float = Field(default=0.55, ge=0.0, le=1.0)
    min_product_fit_score: float = Field(default=0.55, ge=0.0, le=1.0)
    require_head_visible: bool = True
    reject_headwear: bool = False

    @model_validator(mode="after")
    def validate_campaign(self):
        if self.min_head_ratio >= self.max_head_ratio:
            raise ValueError("min_head_ratio must be lower than max_head_ratio")
        self.search_queries = normalize_search_queries(self.search_queries, self.query)
        self.query = self.search_queries[0]
        return self


class CampaignUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    search_queries: list[str] | None = None
    discovery_mode: DiscoveryMode | None = None
    product_context: ProductContextInput | None = None
    target_count: int | None = Field(default=None, ge=1, le=5000)
    max_scroll_batches: int | None = Field(default=None, ge=1, le=50)
    auto_import: bool | None = None
    auto_scout: bool | None = None
    scan_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    min_head_ratio: float | None = Field(default=None, ge=0.05, le=0.90)
    max_head_ratio: float | None = Field(default=None, ge=0.05, le=0.95)
    min_smile_score: float | None = Field(default=None, ge=0.0, le=1.0)
    max_head_occlusion: float | None = Field(default=None, ge=0.0, le=1.0)
    max_ai_risk_score: float | None = Field(default=None, ge=0.0, le=1.0)
    min_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    min_ugc_score: float | None = Field(default=None, ge=0.0, le=1.0)
    min_product_fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    require_head_visible: bool | None = None
    reject_headwear: bool | None = None

    @model_validator(mode="after")
    def validate_campaign(self):
        if self.search_queries is not None:
            self.search_queries = normalize_search_queries(self.search_queries)
        if (
            self.min_head_ratio is not None
            and self.max_head_ratio is not None
            and self.min_head_ratio >= self.max_head_ratio
        ):
            raise ValueError("min_head_ratio must be lower than max_head_ratio")
        return self


class KeywordHealthResponse(BaseModel):
    query: str
    state: str = "explore"
    protected: bool = False
    scans: int = 0
    found: int = 0
    new: int = 0
    duplicate: int = 0
    failed_scans: int = 0
    approved: int = 0
    ref_good: int = 0
    ref_bad: int = 0
    context_good: int = 0
    context_wrong: int = 0
    approved_yield: float = 0.0
    reference_yield: float = 0.0
    duplicate_rate: float = 0.0
    failure_rate: float = 0.0


class CampaignResponse(BaseModel):
    id: str
    name: str
    query: str
    search_queries: list[str] = Field(default_factory=list)
    search_query_anchors: list[str] = Field(default_factory=list)
    discovery_mode: DiscoveryMode = "keyword"
    product_context: dict | None = None
    keyword_health: list[KeywordHealthResponse] = Field(default_factory=list)
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    auto_scout: bool
    scan_interval_seconds: int
    scan_next_at: datetime | None = None
    scan_last_started_at: datetime | None = None
    scan_last_completed_at: datetime | None = None
    scan_attempt_count: int = 0
    scan_empty_streak: int = 0
    scan_failure_streak: int = 0
    scan_last_error_code: str | None = None
    active_scan_run_id: str | None = None
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
    product_source_url: str | None = None
    product_brand: str | None = None
    product_revision: int | None = None
    product_variant_ids: list[str] = Field(default_factory=list)
    product_variants: list[ProductVariantResponse] = Field(default_factory=list)
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


class SourcePlanReferencePreviewResponse(BaseModel):
    id: str
    pin_url: str
    image_url: str
    status: str
    picked: bool = False
    rejected: bool = False
    source_query: str | None = None
    width: int | None = None
    height: int | None = None
    created_at: datetime


class SourcePlanGroupImageResponse(BaseModel):
    id: str
    source_name: str
    source_relative_path: str
    source_preview_url: str
    source_web_url: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_size_bytes: int | None = None


class KeywordVolumeTrendPointResponse(BaseModel):
    period: str
    volume: int = 0


class KeywordVolumeResponse(BaseModel):
    id: str
    keyword: str
    search_volume: int = 0
    competition: str | None = None
    cpc_low: float | None = None
    cpc_high: float | None = None
    trademark_status: Literal["unverified", "safe", "warning", "danger", "possible_match", "no_exact_match"] = "unverified"
    trademark_checked_at: datetime | None = None
    trademark_source: str | None = None
    trademark_match_count: int | None = None
    trademark_class_025: bool | None = None
    trademark_category: str | None = None
    trademark_advice: str | None = None
    trademark_details: str | None = None
    trademark_primary_conflict: dict | None = None
    trademark_matches: list[dict] = Field(default_factory=list)
    trademark_screened_keyword: str | None = None
    competition_index: int | None = None
    three_month_change_pct: float | None = None
    yoy_change_pct: float | None = None
    trend: list[KeywordVolumeTrendPointResponse] = Field(default_factory=list)
    source_image_url: str | None = None
    source_pin_url: str | None = None
    scout_keyword_feedback: Literal["suggested", "blocked", "neutral"] = "neutral"
    scout_pin_feedback: Literal["suggested", "blocked", "neutral"] = "neutral"
    picked: bool = False
    picked_at: datetime | None = None
    favorite: bool = False
    favorite_at: datetime | None = None
    provider: str
    provider_account: str | None = None
    provider_customer_id: str | None = None
    request_count: int = 0
    fetched_at: datetime
    last_requested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KeywordSuggestionResponse(BaseModel):
    keyword: str
    search_volume: int = 0
    favorite: bool = False
    picked: bool = False


class ScoutFeedbackRequest(BaseModel):
    action: Literal["suggested", "blocked", "neutral"]
    scope: Literal["keyword", "pin", "both"] = "both"


class ScoutFeedbackLeaseRequest(BaseModel):
    id: str = Field(min_length=36, max_length=36)
    lease_token: str = Field(min_length=36, max_length=36)


class ScoutFeedbackFinishRequest(BaseModel):
    lease_token: str = Field(min_length=36, max_length=36)
    id: str = Field(min_length=36, max_length=36)
    success: bool = True


class ScoutQueryCompleteRequest(BaseModel):
    id: str = Field(min_length=36, max_length=36)
    lease_token: str = Field(min_length=36, max_length=36)
    success: bool = True
    scanned_pins: int = Field(default=0, ge=0, le=100_000)
    found_quotes: int = Field(default=0, ge=0, le=100_000)
    new_keywords: int = Field(default=0, ge=0, le=100_000)
    duplicate_pins: int = Field(default=0, ge=0, le=100_000)
    retryable: bool = False  # transient Gemini/Pinterest pause; do not penalize a query


class ScoutMetricCycleRequest(BaseModel):
    machine_label: str = Field(default="unknown", min_length=1, max_length=160)
    mode: Literal["keyword", "review"] = "keyword"
    cycle_id: str = Field(min_length=8, max_length=64)
    scanned_pins: int = Field(default=0, ge=0, le=100_000)
    found_quotes: int = Field(default=0, ge=0, le=100_000)
    new_keywords: int = Field(default=0, ge=0, le=100_000)
    duplicate_pins: int = Field(default=0, ge=0, le=100_000)
    errors: int = Field(default=0, ge=0, le=100_000)


class KeywordVolumePickRequest(BaseModel):
    picked: bool = True


class KeywordVolumeFavoriteRequest(BaseModel):
    favorite: bool = True


class KeywordVolumeResolveRequest(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=50)
    force: bool = False
    source_image_url: str | None = Field(default=None, max_length=2048)
    source_pin_url: str | None = Field(default=None, max_length=2048)


class KeywordVolumeResolveResponse(BaseModel):
    requested: int
    provider_requested: int
    cached: int
    items: list[KeywordVolumeResponse] = Field(default_factory=list)


class QuoteScoutAnalyzeRequest(BaseModel):
    pin_url: str | None = Field(default=None, max_length=2048)
    image_url: str = Field(min_length=1, max_length=2048)
    alt_text: str | None = Field(default=None, max_length=1000)
    image_base64: str | None = Field(default=None, max_length=17_000_000)
    image_mime_type: str | None = Field(default=None, max_length=64)


class QuoteScoutAnalyzeResponse(BaseModel):
    quotes: list[str] = Field(default_factory=list)
    is_target_cap: bool = False
    confidence: float = 0.0
    provider: str
    model: str | None = None


class KeywordVolumeOverviewResponse(BaseModel):
    total_keywords: int = 0
    total_search_volume: int = 0
    average_search_volume: float = 0.0
    average_cpc: float | None = None
    high_competition: int = 0
    zero_volume: int = 0
    short_tail_keywords: int = 0
    mid_tail_keywords: int = 0
    long_tail_keywords: int = 0
    picked_keywords: int = 0
    favorite_keywords: int = 0


class ScoutKeywordSummaryResponse(BaseModel):
    total_keywords: int = 0
    added_24h: int = 0
    added_7d: int = 0
    analysis_pending: int = 0
    analysis_oldest_wait_seconds: int = 0
    analysis_backpressure_active: bool = False
    keyword_fair_share_limited: bool = False
    keyword_next_slot_seconds: int = 0
    gemini_backup_keys_configured: int = 0
    last_created_at: datetime | None = None
    last_updated_at: datetime | None = None
    fetched_at: datetime


class KeywordVolumePageResponse(BaseModel):
    items: list[KeywordVolumeResponse] = Field(default_factory=list)
    page: int
    page_size: int
    total: int
    overview: KeywordVolumeOverviewResponse = Field(
        default_factory=KeywordVolumeOverviewResponse
    )


class SourcePlanResponse(BaseModel):
    id: str
    root_folder_id: str
    source_file_id: str
    source_parent_folder_id: str | None = None
    source_relative_path: str
    source_name: str
    source_mime_type: str
    source_size_bytes: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_modified_at: datetime | None = None
    source_web_url: str | None = None
    source_preview_url: str
    source_revision: str
    analysis_revision: int
    embroidery_signature: str | None = None
    embroidery_group_size: int = 1
    source_group_images: list[SourcePlanGroupImageResponse] = Field(default_factory=list)
    target_count: int
    status: str
    visual_context: dict | None = None
    reference_contexts: list[str] = Field(default_factory=list)
    campaign_id: str | None = None
    campaign_name: str | None = None
    campaign_status: str | None = None
    scout_status: str | None = None
    auto_scout: bool = False
    search_queries: list[str] = Field(default_factory=list)
    progress_count: int = 0
    pipeline_count: int = 0
    candidate_count: int = 0
    approved_count: int = 0
    pending_ai_count: int = 0
    drive_ready_count: int = 0
    scan_next_at: datetime | None = None
    scan_last_completed_at: datetime | None = None
    reference_previews: list[SourcePlanReferencePreviewResponse] = Field(default_factory=list)
    last_error_code: str | None = None
    analyzed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SourcePlanOverviewResponse(BaseModel):
    embroidery_groups: int = 0
    source_images: int = 0
    working_groups: int = 0
    refs_loaded: int = 0
    stage2_groups: int = 0
    stage2_source_images: int = 0
    stage2_drive_ready_refs: int = 0
    stage2_active_jobs: int = 0


class SourcePlanPageResponse(BaseModel):
    items: list[SourcePlanResponse] = Field(default_factory=list)
    page: int
    page_size: int
    total: int
    overview: SourcePlanOverviewResponse = Field(default_factory=SourcePlanOverviewResponse)


class SourcePlanSyncResponse(BaseModel):
    root_folder_id: str
    target_count: int = 50
    folders_scanned: int
    images_found: int
    plans_created: int
    plans_updated: int
    plans_missing: int
    jobs_queued: int
    unchanged: int


class CampaignScoutAutomationRequest(BaseModel):
    auto_scout: bool = True
    scan_interval_seconds: int = Field(default=300, ge=60, le=86400)


class ScoutAgentCreateRequest(BaseModel):
    name: str = Field(default="Pinterest Auto Scout", min_length=1, max_length=160)


class ScoutAgentResponse(BaseModel):
    id: str
    name: str
    status: ScoutStatus
    active: bool
    client_version: str | None = None
    machine_label: str | None = None
    last_error_code: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ScoutAgentCreatedResponse(ScoutAgentResponse):
    agent_token: str


class RrugcHealthResponse(BaseModel):
    orphan_analysis_queued: int = 0
    stale_importing: int = 0
    gemini_deferred: int = 0
    oldest_analysis_queue_age_seconds: int | None = None
    scout_total: int = 0
    scout_online: int = 0
    scout_offline: int = 0
    scout_outdated: int = 0
    gemini_capacity_available: bool = False
    checked_at: datetime


class ScoutAgentHeartbeatRequest(BaseModel):
    status: Literal["ready", "busy", "needs_login", "error"]
    client_version: str | None = Field(default=None, max_length=64)
    machine_label: str | None = Field(default=None, max_length=160)
    run_id: str | None = Field(default=None, max_length=36)
    error_code: str | None = Field(default=None, max_length=100)


class ScoutLogEventRequest(BaseModel):
    event_id: str = Field(min_length=8, max_length=255)
    event_type: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    level: Literal["trace", "debug", "info", "warning", "error", "critical"] = "info"
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class ScoutLogBatchRequest(BaseModel):
    events: list[ScoutLogEventRequest] = Field(min_length=1, max_length=100)


class ScoutLogBatchResponse(BaseModel):
    accepted: int
    created: int
    retention_days: int


class ScoutRunResponse(BaseModel):
    id: str
    campaign_id: str
    agent_id: str
    status: Literal["claimed", "running", "completed", "needs_login", "failed", "cancelled"]
    query: str
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    progress_before: int
    submitted_count: int
    created_count: int
    existing_count: int
    last_error_code: str | None = None
    last_heartbeat_at: datetime | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScoutRelatedSeed(BaseModel):
    pin_url: str = Field(min_length=1, max_length=2048)
    image_url: str = Field(min_length=1, max_length=4096)
    alt_text: str | None = Field(default=None, max_length=2000)


class ScoutQueryPerformance(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    score: float = 0.0
    submitted: int = 0
    created: int = 0
    existing: int = 0
    approved: int = 0
    rejected: int = 0
    needs_review: int = 0
    stage2_used: int = 0


class ScoutClaimResponse(BaseModel):
    run: ScoutRunResponse
    campaign_id: str
    query: str
    search_queries: list[str] = Field(default_factory=list)
    query_performance: list[ScoutQueryPerformance] = Field(default_factory=list)
    known_pin_urls: list[str] = Field(default_factory=list)
    target_count: int
    max_scroll_batches: int
    auto_import: bool
    progress: int
    pipeline_count: int
    source_plan_id: str | None = None
    source_file_id: str | None = None
    source_relative_path: str | None = None
    source_name: str | None = None
    source_context: dict | None = None
    related_seeds: list[ScoutRelatedSeed] = Field(default_factory=list)



class Stage2SkillResponse(BaseModel):
    source: Literal["local", "openai"]
    skill_id: str | None = None
    skill_name: str
    display_name: str
    description: str
    default_version: str | None = None
    latest_version: str | None = None
    local_version: str | None = None
    synced_version: str | None = None
    ready: bool
    sync_state: Literal["ready", "not_synced", "update_available", "local_conflict"]
    version_options: list[str] = Field(default_factory=list)


class Stage2SkillCatalogResponse(BaseModel):
    openai_configured: bool
    openai_status: Literal["not_configured", "connected", "error"]
    error_code: str | None = None
    items: list[Stage2SkillResponse] = Field(default_factory=list)


class Stage2SkillSyncRequest(BaseModel):
    version: str | None = Field(default=None, min_length=1, max_length=64)


class Stage2SkillRegistryVersionResponse(BaseModel):
    id: str
    version: str
    is_default: bool
    is_synced: bool
    status: str
    bundle_sha256: str | None = None
    created_at: datetime


class Stage2SkillRegistryItemResponse(BaseModel):
    id: str
    source: Literal["local", "openai"]
    skill_id: str | None = None
    skill_name: str
    display_name: str
    description: str
    workflow: str
    enabled: bool
    default_version: str | None = None
    latest_version: str | None = None
    synced_version: str | None = None
    sync_state: str
    validation_status: str
    bundle_sha256: str | None = None
    last_error: str | None = None
    versions: list[Stage2SkillRegistryVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class Stage2SkillRegistryResponse(BaseModel):
    can_manage: bool
    items: list[Stage2SkillRegistryItemResponse] = Field(default_factory=list)


class Stage2SkillEnabledRequest(BaseModel):
    enabled: bool


class Stage2SkillDefaultVersionRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)


class Stage2JobCreateRequest(BaseModel):
    selected_candidate_ids: list[str] = Field(min_length=1, max_length=3)
    skill_source: Literal["local", "openai"] | None = None
    skill_id: str | None = Field(default=None, min_length=1, max_length=255)
    skill_name: str | None = Field(default=None, min_length=1, max_length=128)
    skill_version: str | None = Field(default=None, min_length=1, max_length=64)
    prompt: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_selected_candidates(self):
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in self.selected_candidate_ids:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        if not cleaned:
            raise ValueError("Pick at least one Pinterest reference")
        if len(cleaned) > 3:
            raise ValueError("Pick at most 3 Pinterest references per generation run")
        self.selected_candidate_ids = cleaned
        return self


class Stage2JobResponse(BaseModel):
    id: str
    source_plan_id: str
    campaign_id: str
    source_revision: str
    skill_name: str
    skill_source: Literal["local", "openai"] = "local"
    skill_id: str | None = None
    skill_version: str | None = None
    skill_bundle_sha256: str | None = None
    selected_candidate_ids: list[str] = Field(default_factory=list)
    reference_count: int = 0
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    can_cancel: bool = False
    cancel_available_until: datetime | None = None
    processing_job_id: str | None = None
    provider_request_id: str | None = None
    output_content_type: str | None = None
    output_size_bytes: int | None = None
    output_width: int | None = None
    output_height: int | None = None
    output_remote_file_id: str | None = None
    output_web_url: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Stage2JobCreatedResponse(BaseModel):
    created: bool
    job: Stage2JobResponse


class Stage2JobsCancelledResponse(BaseModel):
    cancelled: int
    job_ids: list[str] = Field(default_factory=list)


class Stage3ReviewImageResponse(BaseModel):
    stage2_job_id: str
    source_plan_id: str
    source_name: str
    source_relative_path: str
    output_remote_file_id: str
    output_width: int | None = None
    output_height: int | None = None
    output_content_type: str | None = None
    completed_at: datetime | None = None
    preview_url: str
    original_url: str
    analysis_id: str | None = None
    analysis_status: Literal[
        "pending", "queued", "analyzing", "ready", "rejected", "error"
    ] = "pending"
    final_score: float | None = None
    mobile_ugc_score: float | None = None
    photorealism_score: float | None = None
    product_visibility_score: float | None = None
    review_fit_score: float | None = None
    person_visible: bool | None = None
    hat_visible: bool | None = None
    product_visible: bool | None = None
    embroidery_visible: bool | None = None
    scene_type: str | None = None
    framing_type: str | None = None
    summary: str | None = None
    reviewer_name: str | None = None
    star_rating: int | None = Field(default=None, ge=1, le=5)
    review_text: str | None = None
    review_generated_at: datetime | None = None
    reject_reasons: list[str] = Field(default_factory=list)
    last_error_code: str | None = None


class Stage3ReviewGroupResponse(BaseModel):
    folder_id: str
    folder_name: str
    folder_path: str
    image_count: int
    status: Literal[
        "pending", "analyzing", "ready", "partial", "rejected", "error"
    ] = "pending"
    ready_count: int = 0
    rejected_count: int = 0
    analyzing_count: int = 0
    pending_count: int = 0
    error_count: int = 0
    latest_completed_at: datetime | None = None
    images: list[Stage3ReviewImageResponse] = Field(default_factory=list)


class Stage3ReviewGroupListResponse(BaseModel):
    items: list[Stage3ReviewGroupResponse] = Field(default_factory=list)
    total_groups: int
    total_images: int
    ready_images: int = 0
    rejected_images: int = 0
    analyzing_images: int = 0
    pending_images: int = 0
    error_images: int = 0


class Stage3AnalyzeRequest(BaseModel):
    folder_id: str | None = Field(default=None, max_length=255)
    force: bool = False


class Stage3AnalyzeResponse(BaseModel):
    eligible: int
    queued: int
    existing: int


class ScoutRunCompleteRequest(BaseModel):
    status: Literal["completed", "needs_login", "failed"]
    error_code: str | None = Field(default=None, max_length=100)


class CampaignProductBindRequest(BaseModel):
    product_id: str | None = Field(default=None, max_length=36)
    variant_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


class GenerationAttemptCreateRequest(BaseModel):
    generation_variant: int = Field(default=1, ge=1, le=20)
    worker_skill_version: str | None = Field(default=None, min_length=1, max_length=128)
    reference_set_id: str | None = Field(default=None, max_length=36)


class GenerationSkillResponse(BaseModel):
    skill_name: str
    display_name: str
    description: str
    workflows: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    required_reference_roles: list[str] = Field(default_factory=list)
    optional_reference_roles: list[str] = Field(default_factory=list)
    max_references: int
    recommended: bool = False


class GenerationSkillCatalogResponse(BaseModel):
    recommended_skill_name: str | None = None
    items: list[GenerationSkillResponse] = Field(default_factory=list)


class GenerationCapabilityResponse(BaseModel):
    enabled: bool
    available: bool
    provider: Literal["gemini", "codex"]
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
    reference_set_id: str | None = None
    reference_roles: list[str] = Field(default_factory=list)
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
    # Compatibility with Review Scout v33: a detail-resolution batch can become
    # empty after videos/unresolvable Pins are filtered. Treat that as a no-op
    # instead of rejecting the whole run with HTTP 422. Scout v34 also avoids
    # sending these requests client-side.
    items: list[CandidateSubmission] = Field(max_length=50)
    source_query: str | None = Field(default=None, min_length=1, max_length=500)


class AutoScoutCandidateBatchRequest(CandidateBatchRequest):
    """Tolerate incomplete Pinterest detail fallbacks from running v38 clients.

    This compatibility repair applies ONLY to token-authenticated auto agents.
    General candidate ingestion retains the original strict schema. Nonempty
    URLs still receive full URL allowlist validation in the ingestion service.
    """

    @model_validator(mode="before")
    @classmethod
    def sanitize_legacy_auto_scout_batch(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        raw_items = value.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > 50:
            return value
        clean_items = []
        for item in raw_items:
            if not isinstance(item, dict):
                clean_items.append(item)
                continue
            image_url = item.get("image_url")
            if image_url is None or (isinstance(image_url, str) and not image_url.strip()):
                continue
            clean = dict(item)
            if isinstance(clean.get("alt_text"), str):
                clean["alt_text"] = clean["alt_text"][:2000]
            clean_items.append(clean)
        clean_value = dict(value)
        clean_value["items"] = clean_items
        if isinstance(value.get("source_query"), str):
            clean_value["source_query"] = " ".join(
                value["source_query"].split()
            )[:500] or None
        return clean_value


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
    phone_authenticity_score: float | None = None
    artistic_editorial_risk: float | None = None
    quality_score: float | None
    ai_risk_score: float | None
    ai_risk_raw_score: float | None = None
    ai_detector_confidence: float | None = None
    ai_risk_confirmed: bool | None = None
    ai_signal_json: dict | None = None
    ai_manual_label: AiManualLabel | None = None
    ai_manual_note: str | None = None
    ai_manual_reviewed_by_user_id: str | None = None
    ai_manual_reviewed_at: datetime | None = None
    reference_manual_label: ReferenceManualLabel | None = None
    reference_manual_note: str | None = None
    reference_manual_reviewed_by_user_id: str | None = None
    reference_manual_reviewed_at: datetime | None = None
    context_manual_label: Literal["good", "wrong"] | None = None
    context_manual_note: str | None = None
    context_manual_reviewed_by_user_id: str | None = None
    context_manual_reviewed_at: datetime | None = None
    product_fit_score: float | None
    context_match_active: bool = False
    context_match_score: float | None = None
    context_match_evidence: list[str] = Field(default_factory=list)
    matched_variant_id: str | None = None
    matched_variant_name: str | None = None
    matched_color: str | None = None
    color_match_score: float | None = None
    product_shape_score: float | None = None
    final_score: float | None
    ranking_score: float | None = None
    source_query: str | None = None
    context_feedback_adjustment: float = 0.0
    context_feedback_direction: Literal["boost", "downrank"] | None = None
    context_feedback_reviews: int = 0
    seed_visual_active: bool = False
    seed_visual_score: float | None = None
    seed_visual_adjustment: float = 0.0
    seed_visual_positive_similarity: float | None = None
    seed_visual_negative_similarity: float | None = None
    seed_visual_positive_count: int = 0
    seed_visual_negative_count: int = 0
    seed_visual_profile_key: str | None = None
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


class ReferenceAssetResponse(BaseModel):
    id: str
    source_type: str
    source_key: str
    source_url: str | None = None
    original_filename: str | None = None
    source_campaign_id: str | None = None
    source_candidate_id: str | None = None
    profile_key: str | None = None
    reference_type: Literal["person", "product", "scene", "detail", "artwork", "other"]
    status: str
    content_hash: str
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    image_format: str | None = None
    tags: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    quality_score: float | None = None
    visual_score: float | None = None
    context_score: float | None = None
    usage_count: int = 0
    remote_file_id: str
    remote_folder_id: str | None = None
    web_url: str | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ReferenceAssetPromotionResponse(BaseModel):
    asset: ReferenceAssetResponse
    created: bool


class ReferenceSeedRequest(BaseModel):
    label: ReferenceSeedLabel
    profile_key: str = Field(default="realistic-person-ugc", min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class ReferenceSeedResponse(BaseModel):
    id: str
    campaign_id: str
    reference_asset_id: str
    profile_key: str
    label: ReferenceSeedLabel
    note: str | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class ReferenceSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=36)
    profile_key: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class ReferenceSetSkillPresetItemRequest(BaseModel):
    reference_asset_id: str = Field(min_length=1, max_length=36)
    role: str = Field(min_length=1, max_length=100)


class ReferenceSetSkillPresetCreateRequest(BaseModel):
    skill_name: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=36)
    profile_key: str | None = Field(default=None, max_length=100)
    items: list[ReferenceSetSkillPresetItemRequest] = Field(min_length=1, max_length=32)


class ReferenceSetRecommendationItemResponse(BaseModel):
    role: str
    required: bool
    reference_asset: ReferenceAssetResponse | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    learning_adjustment: float = 0.0
    review_approved_count: int = 0
    review_rejected_count: int = 0


class ReferenceSetReuseRecommendationResponse(BaseModel):
    reference_set_id: str | None = None
    reference_set_name: str | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    review_approved_count: int = 0
    review_rejected_count: int = 0


class ReferenceSetFeedbackResponse(BaseModel):
    reference_set_id: str
    reference_set_name: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    review_approved_count: int = 0
    review_rejected_count: int = 0


class ReferenceSetRecommendationResponse(BaseModel):
    campaign_id: str
    skill_name: str
    suggested_name: str
    complete: bool
    missing_required_roles: list[str] = Field(default_factory=list)
    learning_review_count: int = 0
    learning_applied: bool = False
    reuse_recommendation: ReferenceSetReuseRecommendationResponse
    discouraged_reference_sets: list[ReferenceSetFeedbackResponse] = Field(
        default_factory=list
    )
    items: list[ReferenceSetRecommendationItemResponse] = Field(default_factory=list)


class ReferenceSetItemCreateRequest(BaseModel):
    reference_asset_id: str = Field(min_length=1, max_length=36)
    role: str = Field(min_length=1, max_length=100)
    position: int = Field(default=0, ge=0, le=1000)
    note: str | None = Field(default=None, max_length=1000)


class ReferenceSetItemResponse(BaseModel):
    id: str
    reference_asset_id: str
    role: str
    position: int
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class ReferenceSetResponse(BaseModel):
    id: str
    name: str
    campaign_id: str | None = None
    profile_key: str | None = None
    description: str | None = None
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    items: list[ReferenceSetItemResponse] = Field(default_factory=list)


class ReferenceSetItemBindingResponse(BaseModel):
    reference_set: ReferenceSetResponse
    item: ReferenceSetItemResponse
    created: bool


class CandidateAiFeedbackRequest(BaseModel):
    label: AiManualLabel
    note: str | None = Field(default=None, max_length=1000)


class AiFeedbackCalibrationResponse(BaseModel):
    active: bool
    real_count: int
    ai_count: int
    real_mean: float | None = None
    ai_mean: float | None = None


class CandidateAiFeedbackResponse(BaseModel):
    candidate: CandidateResponse
    calibration: AiFeedbackCalibrationResponse


class CandidateReferenceFeedbackRequest(BaseModel):
    label: Literal["good", "bad", "ai", "clear"]
    profile_key: str = Field(
        default="realistic-person-ugc",
        min_length=1,
        max_length=100,
    )
    note: str | None = Field(default=None, max_length=1000)


class ReferencePreferenceLearningResponse(BaseModel):
    active: bool
    good_count: int
    bad_count: int


class CandidateReferenceFeedbackResponse(BaseModel):
    candidate: CandidateResponse
    learning: ReferencePreferenceLearningResponse


class CandidateContextFeedbackRequest(BaseModel):
    label: Literal["good", "wrong", "clear"]
    note: str | None = Field(default=None, max_length=1000)


class CandidateContextFeedbackResponse(BaseModel):
    candidate: CandidateResponse


class CandidateBatchResponse(BaseModel):
    created: int
    existing: int
    items: list[CandidateResponse]


class AutoScoutCandidateBatchResponse(BaseModel):
    created: int
    existing: int
    progress: int
    pipeline_count: int
    target_count: int
    campaign_status: CampaignStatus


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
