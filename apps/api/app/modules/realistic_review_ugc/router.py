from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.image_generation.providers import GEMINI_IMAGE_MODEL
from app.modules.image_generation.service import provider_capability
from app.modules.realistic_review_ugc.analysis import (
    build_ai_risk_calibration,
    build_reference_preference_model,
)
from app.modules.realistic_review_ugc.keyword_strategy import campaign_learning_intent
from app.modules.realistic_review_ugc.generation import (
    RrugcGenerationFoundation,
    binding_is_generation_ready,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcGenerationAttemptModel,
    RrugcSupervisorResultModel,
    RrugcReviewTaskModel,
    RrugcExportModel,
    RrugcScoutAgentModel,
    RrugcScoutRunModel,
    RrugcDeliveryDestinationModel,
    RrugcDeliveryPackageModel,
    RrugcDeliveryEventModel,
    RrugcDeliveryItemModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
    RrugcProductVariantModel,
)
from app.modules.realistic_review_ugc.product_page_import import (
    ProductPageImportError,
    fetch_product_image,
    fetch_product_page,
)
from app.modules.realistic_review_ugc.product_registry import (
    PRODUCT_REFERENCE_MAX_BYTES,
    RrugcProductRegistry,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import (
    AnalyzeResponse,
    CampaignCreatedResponse,
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignProductBindRequest,
    CampaignResponse,
    CampaignScoutAutomationRequest,
    CandidateBatchRequest,
    AutoScoutCandidateBatchResponse,
    CandidateBatchResponse,
    CandidateResponse,
    CandidateAiFeedbackRequest,
    CandidateAiFeedbackResponse,
    AiFeedbackCalibrationResponse,
    CandidateReferenceFeedbackRequest,
    CandidateReferenceFeedbackResponse,
    ReferencePreferenceLearningResponse,
    GenerationAttemptCreateRequest,
    GenerationAttemptCreatedResponse,
    GenerationAttemptResponse,
    GenerationCapabilityResponse,
    ImportResponse,
    SupervisorResultResponse,
    ReviewTaskDecisionRequest,
    ReviewTaskListResponse,
    ReviewTaskReconcileResponse,
    ReviewTaskResponse,
    ReviewTaskTransitionResponse,
    ExportResponse,
    ExportListResponse,
    BatchExportResponse,
    CampaignExportSummaryResponse,
    DeliveryDestinationCreateRequest,
    DeliveryDestinationResponse,
    DeliveryItemResponse,
    DeliveryPackageResponse,
    DeliveryPackageListResponse,
    CampaignLifecyclePolicyRequest,
    CampaignDeliverySummaryResponse,
    DeliveryLifecycleReconcileResponse,
    DeliveryEventResponse,
    DeliveryOperationsSummaryResponse,
    DeliveryMaintenanceEnqueueResponse,
    ProductCreateRequest,
    ProductReferenceResponse,
    ProductReferenceView,
    ProductResponse,
    ProductUpdateRequest,
    ProductVariantResponse,
    ProductVariantUpdateRequest,
    ProductUrlImportItemResponse,
    ProductUrlImportRequest,
    ProductUrlImportResponse,
    ScoutAgentCreateRequest,
    ScoutAgentResponse,
    ScoutAgentCreatedResponse,
    ScoutAgentHeartbeatRequest,
    ScoutClaimResponse,
    ScoutRunCompleteRequest,
    ScoutRunResponse,
    ScoutHeartbeatRequest,
    ScoutTaskResponse,
)
from app.modules.realistic_review_ugc.review import RrugcReviewService
from app.modules.realistic_review_ugc.scout_automation import (
    RrugcAutoScoutService,
    effective_agent_status,
    keyword_health_rows,
)
from app.modules.realistic_review_ugc.export import RrugcExportService
from app.modules.realistic_review_ugc.delivery import RrugcDeliveryService
from app.modules.realistic_review_ugc.delivery_automation import (
    RrugcDeliveryMaintenanceScheduler,
)
from app.modules.realistic_review_ugc.supervisor import (
    MAX_GENERATION_ATTEMPTS,
    RrugcSupervisorService,
)
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    campaign_token_matches,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


router = APIRouter(
    prefix="/api/v1/realistic-review-ugc",
    tags=["realistic-review-ugc"],
)
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")

ANALYSIS_APPROVED_STATUSES = {
    "approved",
    "import_queued",
    "importing",
    "drive_ready",
    "import_failed",
}
ANALYSIS_REJECTED_STATUSES = {
    "rejected_no_person",
    "rejected_head_ratio",
    "rejected_expression",
    "rejected_existing_headwear",
    "rejected_head_occlusion",
    "rejected_quality",
    "rejected_ai_risk",
    "rejected_context",
}


def _error(exc: RrugcError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _candidate(row: RrugcCandidateModel) -> CandidateResponse:
    signal = row.ai_signal_json if isinstance(row.ai_signal_json, dict) else {}
    return CandidateResponse.model_validate({
        "id": row.id,
        "campaign_id": row.campaign_id,
        "pin_url": row.pin_url,
        "image_url": row.image_url,
        "alt_text": row.alt_text,
        "status": row.status,
        "analysis_revision": row.analysis_revision,
        "import_revision": row.import_revision,
        "people_count": row.people_count,
        "primary_head_ratio": row.primary_head_ratio,
        "smile_score": row.smile_score,
        "head_visible": row.head_visible,
        "existing_headwear": row.existing_headwear,
        "head_occlusion": row.head_occlusion,
        "mobile_ugc_score": row.mobile_ugc_score,
        "phone_authenticity_score": row.phone_authenticity_score,
        "artistic_editorial_risk": row.artistic_editorial_risk,
        "quality_score": row.quality_score,
        "ai_risk_score": row.ai_risk_score,
        "ai_risk_raw_score": row.ai_risk_raw_score,
        "ai_detector_confidence": row.ai_detector_confidence,
        "ai_risk_confirmed": row.ai_risk_confirmed,
        "ai_signal_json": row.ai_signal_json,
        "ai_manual_label": row.ai_manual_label,
        "ai_manual_note": row.ai_manual_note,
        "ai_manual_reviewed_by_user_id": row.ai_manual_reviewed_by_user_id,
        "ai_manual_reviewed_at": row.ai_manual_reviewed_at,
        "reference_manual_label": signal.get("reference_manual_label"),
        "reference_manual_note": signal.get("reference_manual_note"),
        "reference_manual_reviewed_by_user_id": signal.get("reference_manual_reviewed_by_user_id"),
        "reference_manual_reviewed_at": signal.get("reference_manual_reviewed_at"),
        "product_fit_score": row.product_fit_score,
        "matched_variant_id": row.matched_variant_id,
        "matched_variant_name": row.matched_variant_name,
        "matched_color": row.matched_color,
        "color_match_score": row.color_match_score,
        "product_shape_score": row.product_shape_score,
        "final_score": row.final_score,
        "reject_reason": row.reject_reason,
        "analyzer_provider": row.analyzer_provider,
        "analyzer_model": row.analyzer_model,
        "analyzer_version": row.analyzer_version,
        "analysis_summary": row.analysis_summary,
        "analyzed_at": row.analyzed_at,
        "content_hash": row.content_hash,
        "width": row.width,
        "height": row.height,
        "size_bytes": row.size_bytes,
        "remote_file_id": row.remote_file_id,
        "remote_folder_id": row.remote_folder_id,
        "web_url": row.web_url,
        "last_error_code": row.last_error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })


def _product_reference(row: RrugcProductReferenceModel) -> ProductReferenceResponse:
    return ProductReferenceResponse(
        id=row.id,
        product_id=row.product_id,
        variant_id=row.variant_id,
        view_type=row.view_type,
        version=row.version,
        status=row.status,
        content_hash=row.content_hash,
        original_filename=row.original_filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        width=row.width,
        height=row.height,
        image_format=row.image_format,
        remote_file_id=row.remote_file_id,
        remote_folder_id=row.remote_folder_id,
        web_url=row.web_url,
        reused_storage=row.reused_storage,
        created_at=row.created_at,
        archived_at=row.archived_at,
    )


def _product_variant(
    row: RrugcProductVariantModel,
    *,
    reference_count: int = 0,
) -> ProductVariantResponse:
    return ProductVariantResponse(
        id=row.id,
        product_id=row.product_id,
        source_variant_id=row.source_variant_id,
        sku=row.sku,
        name=row.name,
        color=row.color,
        size=row.size,
        price_text=row.price_text,
        currency=row.currency,
        image_urls=list(row.image_urls_json or []),
        available=row.available,
        enabled=row.enabled,
        status=row.status,
        position=row.position,
        reference_count=reference_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _product(repository: RrugcRepository, row: RrugcProductModel) -> ProductResponse:
    references = repository.list_product_references(row.tenant_id, row.id)
    variants = repository.list_product_variants(row.tenant_id, row.id)
    reference_counts = Counter(
        reference.variant_id
        for reference in references
        if reference.variant_id is not None
    )
    views = sorted({reference.view_type for reference in references})
    return ProductResponse(
        id=row.id,
        sku=row.sku,
        name=row.name,
        product_type=row.product_type,
        color=row.color,
        material=row.material,
        crown_profile=row.crown_profile,
        crown_height_mm=row.crown_height_mm,
        brim_style=row.brim_style,
        brim_length_mm=row.brim_length_mm,
        circumference_mm=row.circumference_mm,
        logo_position=row.logo_position,
        fit_notes=row.fit_notes,
        source_url=row.source_url,
        source_host=row.source_host,
        brand=row.brand,
        source_description=row.source_description,
        source_category=row.source_category,
        source_price_text=row.source_price_text,
        source_currency=row.source_currency,
        source_images=list(row.source_images_json or []),
        source_variants=list(row.source_variants_json or []),
        variants=[
            _product_variant(
                variant,
                reference_count=int(reference_counts.get(variant.id, 0)),
            )
            for variant in variants
        ],
        source_metadata=dict(row.source_metadata_json or {}),
        source_fetched_at=row.source_fetched_at,
        revision=row.revision,
        status=row.status,
        reference_count=len(references),
        active_views=views,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _require_product(
    repository: RrugcRepository, tenant_id: str, product_id: str
) -> RrugcProductModel:
    row = repository.get_product(tenant_id, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return row


async def _read_product_reference_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > PRODUCT_REFERENCE_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "product_reference_too_large",
                    "message": "Product reference image exceeds the 20 MB limit.",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _count_sum(counts: Counter, statuses: set[str]) -> int:
    return sum(int(counts.get(status, 0)) for status in statuses)




def _rrugc_generation_capability(
    session: Session, tenant_id: str
) -> GenerationCapabilityResponse:
    settings = get_settings()
    image_capability = provider_capability(session, settings, tenant_id)
    gemini = next(
        (item for item in image_capability.providers if item.id == "gemini"),
        None,
    )
    storage = build_managed_storage_provider(settings)
    storage_available = (
        settings.MANAGED_ASSET_STORAGE_ENABLED
        and not isinstance(storage, UnconfiguredAssetStorageProvider)
    )
    enabled = bool(
        settings.PROCESSING_JOBS_ENABLED
        and settings.IMAGE_GENERATION_ENABLED
        and settings.GEMINI_IMAGE_GENERATION_ENABLED
        and settings.MANAGED_ASSET_STORAGE_ENABLED
    )
    available = bool(enabled and gemini and gemini.available and storage_available)
    reason = None
    if not enabled:
        reason = "Reference-conditioned generation is disabled by production settings."
    elif gemini is None or not gemini.available:
        reason = "Gemini image credentials are not configured for this tenant."
    elif not storage_available:
        reason = "Managed Drive is unavailable."
    return GenerationCapabilityResponse(
        enabled=enabled,
        available=available,
        provider="gemini",
        model=GEMINI_IMAGE_MODEL,
        operation="reference_conditioned_product_edit",
        reason=reason,
    )


def _generation_attempt(row: RrugcGenerationAttemptModel) -> GenerationAttemptResponse:
    product = dict(row.product_snapshot_json or {})
    references = list(row.product_reference_snapshot_json or [])
    return GenerationAttemptResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        candidate_id=row.candidate_id,
        product_id=row.product_id,
        product_revision=row.product_revision,
        product_sku=str(product.get("sku") or ""),
        product_name=str(product.get("name") or ""),
        reference_count=len(references),
        reference_views=sorted({
            str(item.get("view_type"))
            for item in references
            if item.get("view_type")
        }),
        generation_variant=row.generation_variant,
        worker_skill_version=row.worker_skill_version,
        provider=row.provider,
        provider_model=row.provider_model,
        provider_request_id=row.provider_request_id,
        processing_job_id=row.processing_job_id,
        status=row.status,
        output_content_type=row.output_content_type,
        output_size_bytes=row.output_size_bytes,
        output_width=row.output_width,
        output_height=row.output_height,
        output_remote_file_id=row.output_remote_file_id,
        output_web_url=row.output_web_url,
        parent_attempt_id=row.parent_attempt_id,
        correction_supervisor_result_id=row.correction_supervisor_result_id,
        supervisor_correction=(
            dict(row.supervisor_correction_json or {})
            if row.supervisor_correction_json
            else None
        ),
        review_status=row.review_status,
        review_task_id=row.review_task_id,
        reviewed_by_user_id=row.reviewed_by_user_id,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
        export_status=row.export_status,
        export_record_id=row.export_record_id,
        catalog_asset_id=row.catalog_asset_id,
        exported_by_user_id=row.exported_by_user_id,
        exported_at=row.exported_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        queued_at=row.queued_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _supervisor_result(
    repository: RrugcRepository,
    row: RrugcSupervisorResultModel,
) -> SupervisorResultResponse:
    attempts = repository.list_generation_attempts(
        row.tenant_id,
        row.campaign_id,
        candidate_id=row.candidate_id,
        limit=100,
    )
    attempt_count = len(attempts)
    return SupervisorResultResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        generation_attempt_id=row.generation_attempt_id,
        candidate_id=row.candidate_id,
        product_id=row.product_id,
        supervisor_skill_version=row.supervisor_skill_version,
        status=row.status,
        reason=row.reason,
        metrics=dict(row.metrics_json or {}) or None,
        expected=dict(row.expected_json or {}) or None,
        correction=dict(row.correction_json or {}) or None,
        summary=row.summary,
        provider=row.provider,
        provider_model=row.provider_model,
        processing_job_id=row.processing_job_id,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        can_retry=(
            row.status == "fail"
            and bool(row.correction_json)
            and attempt_count < MAX_GENERATION_ATTEMPTS
        ),
        attempt_count=attempt_count,
        max_attempts=MAX_GENERATION_ATTEMPTS,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _review_task(
    repository: RrugcRepository,
    row: RrugcReviewTaskModel,
) -> ReviewTaskResponse:
    attempt = repository.get_generation_attempt(row.tenant_id, row.generation_attempt_id)
    supervisor = repository.get_supervisor_result(row.tenant_id, row.supervisor_result_id)
    campaign = repository.get_campaign(row.tenant_id, row.campaign_id)
    if attempt is None or supervisor is None:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "rrugc_review_provenance_missing",
                "message": "Review task provenance is incomplete.",
            },
        )
    product = dict(attempt.product_snapshot_json or {})
    return ReviewTaskResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        campaign_name=campaign.name if campaign is not None else row.campaign_id,
        candidate_id=row.candidate_id,
        product_id=row.product_id,
        product_sku=str(product.get("sku") or ""),
        product_name=str(product.get("name") or ""),
        generation_attempt_id=row.generation_attempt_id,
        generation_variant=attempt.generation_variant,
        supervisor_result_id=row.supervisor_result_id,
        supervisor_status=supervisor.status,
        supervisor_reason=supervisor.reason,
        supervisor_summary=supervisor.summary,
        supervisor_metrics=dict(supervisor.metrics_json or {}) or None,
        queue_reason=row.queue_reason,
        priority=row.priority,
        status=row.status,
        review_note=row.review_note,
        reviewed_by_user_id=row.reviewed_by_user_id,
        reviewed_at=row.reviewed_at,
        export_status=attempt.export_status or "pending_review",
        output_url=(
            "/api/v1/realistic-review-ugc/generation-attempts/"
            + row.generation_attempt_id
            + "/output"
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _export(row: RrugcExportModel) -> ExportResponse:
    return ExportResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        generation_attempt_id=row.generation_attempt_id,
        review_task_id=row.review_task_id,
        catalog_asset_id=row.catalog_asset_id,
        content_hash=row.content_hash,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        storage_provider=row.storage_provider,
        remote_file_id=row.remote_file_id,
        remote_folder_id=row.remote_folder_id,
        web_url=row.web_url,
        status=row.status,
        requested_by_user_id=row.requested_by_user_id,
        exported_at=row.exported_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_destination(
    row: RrugcDeliveryDestinationModel,
) -> DeliveryDestinationResponse:
    return DeliveryDestinationResponse(
        id=row.id,
        name=row.name,
        kind=row.kind,
        target_ref=row.target_ref,
        retention_days=row.retention_days,
        active=row.active,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _delivery_item(row: RrugcDeliveryItemModel) -> DeliveryItemResponse:
    return DeliveryItemResponse(
        id=row.id,
        export_id=row.export_id,
        catalog_asset_id=row.catalog_asset_id,
        source_remote_file_id=row.source_remote_file_id,
        delivered_remote_file_id=row.delivered_remote_file_id,
        delivered_web_url=row.delivered_web_url,
        status=row.status,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        delivered_at=row.delivered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_package(
    service: RrugcDeliveryService,
    row: RrugcDeliveryPackageModel,
) -> DeliveryPackageResponse:
    return DeliveryPackageResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        destination_id=row.destination_id,
        status=row.status,
        export_count=row.export_count,
        delivered_count=row.delivered_count,
        failed_count=row.failed_count,
        auto_retry_count=row.auto_retry_count,
        items=[
            _delivery_item(item)
            for item in service.package_items(
                tenant_id=row.tenant_id,
                package_id=row.id,
            )
        ],
        started_at=row.started_at,
        last_retry_at=row.last_retry_at,
        next_retry_at=row.next_retry_at,
        delivered_at=row.delivered_at,
        expires_at=row.expires_at,
        expired_at=row.expired_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_event(row: RrugcDeliveryEventModel) -> DeliveryEventResponse:
    return DeliveryEventResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        package_id=row.package_id,
        event_type=row.event_type,
        severity=row.severity,
        message=row.message,
        payload=dict(row.payload_json or {}),
        created_at=row.created_at,
    )


def _campaign(
    repository: RrugcRepository,
    row: RrugcCampaignModel,
    *,
    include_keyword_health: bool = False,
) -> CampaignResponse:
    counts = repository.campaign_counts(row.tenant_id, row.id)
    approved = _count_sum(counts, ANALYSIS_APPROVED_STATUSES)
    rejected = _count_sum(counts, ANALYSIS_REJECTED_STATUSES)
    product = dict(row.product_snapshot_json or {})
    campaign_variants = (
        repository.list_product_variants(row.tenant_id, row.product_id)
        if row.product_id
        else []
    )
    campaign_reference_counts = Counter(
        reference.variant_id
        for reference in (
            repository.list_product_references(row.tenant_id, row.product_id)
            if row.product_id
            else []
        )
        if reference.variant_id is not None
    )
    reference_snapshot = list(row.product_reference_snapshot_json or [])
    reference_views = sorted({
        str(item.get("view_type"))
        for item in reference_snapshot
        if item.get("view_type")
    })
    binding_stale = RrugcGenerationFoundation(repository.session).binding_is_stale(row)
    search_queries = list(row.search_queries_json or [row.query])
    search_query_anchors = list(
        row.search_query_anchors_json
        or [row.query]
    )
    keyword_health = (
        keyword_health_rows(
            search_queries,
            repository.list_scout_runs(
                row.tenant_id,
                campaign_id=row.id,
                limit=100,
            ),
            repository.candidate_keyword_outcomes(
                row.tenant_id,
                row.id,
                limit=2000,
            ),
            protected_queries=search_query_anchors,
        )
        if include_keyword_health
        else []
    )
    return CampaignResponse(
        id=row.id,
        name=row.name,
        query=row.query,
        search_queries=search_queries,
        search_query_anchors=search_query_anchors,
        keyword_health=keyword_health,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        auto_scout=row.auto_scout,
        scan_interval_seconds=row.scan_interval_seconds,
        scan_next_at=row.scan_next_at,
        scan_last_started_at=row.scan_last_started_at,
        scan_last_completed_at=row.scan_last_completed_at,
        scan_attempt_count=row.scan_attempt_count,
        scan_empty_streak=row.scan_empty_streak,
        scan_failure_streak=row.scan_failure_streak,
        scan_last_error_code=row.scan_last_error_code,
        active_scan_run_id=row.scan_lease_run_id,
        min_head_ratio=row.min_head_ratio,
        max_head_ratio=row.max_head_ratio,
        min_smile_score=row.min_smile_score,
        max_head_occlusion=row.max_head_occlusion,
        max_ai_risk_score=row.max_ai_risk_score,
        min_quality_score=row.min_quality_score,
        min_ugc_score=row.min_ugc_score,
        min_product_fit_score=row.min_product_fit_score,
        require_head_visible=row.require_head_visible,
        reject_headwear=row.reject_headwear,
        product_id=row.product_id,
        product_sku=str(product.get("sku") or "") or None,
        product_name=str(product.get("name") or "") or None,
        product_source_url=str(product.get("source_url") or "") or None,
        product_brand=str(product.get("brand") or "") or None,
        product_revision=row.product_revision,
        product_variant_ids=[
            str(value)
            for value in (row.product_variant_ids_json or [])
            if str(value)
        ],
        product_variants=[
            _product_variant(
                variant,
                reference_count=int(campaign_reference_counts.get(variant.id, 0)),
            )
            for variant in campaign_variants
        ],
        product_reference_count=len(reference_snapshot),
        product_reference_views=reference_views,
        product_bound_at=row.product_bound_at,
        product_binding_stale=binding_stale,
        generation_ready=bool(
            row.product_id
            and not binding_stale
            and binding_is_generation_ready(reference_snapshot)
        ),
        auto_complete_on_delivery=row.auto_complete_on_delivery,
        completion_destination_id=row.completion_destination_id,
        completed_at=row.completed_at,
        status=row.status,
        scout_status=row.scout_status,
        scout_last_seen_at=row.scout_last_seen_at,
        discovered=sum(counts.values()),
        analysis_pending=counts.get("analysis_queued", 0),
        analyzing=counts.get("analyzing", 0),
        approved=approved,
        rejected=rejected,
        drive_ready=counts.get("drive_ready", 0),
        failed=counts.get("analysis_failed", 0) + counts.get("import_failed", 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require_campaign(
    repository: RrugcRepository, tenant_id: str, campaign_id: str
) -> RrugcCampaignModel:
    row = repository.get_campaign(tenant_id, campaign_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return row


def _bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _scout_campaign(
    campaign_id: str,
    authorization: str | None,
    session: Session,
) -> RrugcCampaignModel:
    row = RrugcRepository(session).get_campaign_unscoped(campaign_id)
    token = _bearer_token(authorization)
    if row is None or not campaign_token_matches(row, token):
        raise HTTPException(status_code=401, detail="Invalid scout credentials")
    if row.status == "archived":
        raise HTTPException(status_code=410, detail="Campaign is archived")
    return row


def _scout_agent_response(row: RrugcScoutAgentModel) -> ScoutAgentResponse:
    return ScoutAgentResponse(
        id=row.id,
        name=row.name,
        status=effective_agent_status(row),
        active=row.active,
        client_version=row.client_version,
        machine_label=row.machine_label,
        last_error_code=row.last_error_code,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _scout_run_response(row: RrugcScoutRunModel) -> ScoutRunResponse:
    return ScoutRunResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        agent_id=row.agent_id,
        status=row.status,
        query=row.query,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        progress_before=row.progress_before,
        submitted_count=row.submitted_count,
        created_count=row.created_count,
        existing_count=row.existing_count,
        last_error_code=row.last_error_code,
        last_heartbeat_at=row.last_heartbeat_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _agent_token(
    service: RrugcAutoScoutService,
    agent_id: str,
    authorization: str | None,
) -> tuple[RrugcScoutAgentModel, str]:
    token = _bearer_token(authorization)
    try:
        row = service.authenticate_agent(
            agent_id=agent_id,
            raw_token=token,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return row, token


@router.get("/products", response_model=list[ProductResponse])
def list_products(
    include_archived: bool = Query(False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return [
        _product(repository, row)
        for row in repository.list_products(
            principal.active_tenant_id,
            include_archived=include_archived,
        )
    ]


@router.post("/products", response_model=ProductResponse, status_code=201)
def create_product(
    request: ProductCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcProductRegistry(session).create_product(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            request=request,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product(RrugcRepository(session), row)


@router.post("/products/import-urls", response_model=ProductUrlImportResponse)
async def import_product_urls(
    request: ProductUrlImportRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    registry = RrugcProductRegistry(session)
    storage = build_managed_storage_provider(get_settings())
    storage_available = not isinstance(storage, UnconfiguredAssetStorageProvider)
    results: list[ProductUrlImportItemResponse] = []

    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36 CreativeAssetManager/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        trust_env=False,
    ) as client:
        for raw_url in request.urls:
            try:
                page = await fetch_product_page(client, raw_url)
                product, created = registry.upsert_imported_product(
                    tenant_id=principal.active_tenant_id,
                    user_id=principal.user_id,
                    data=page,
                )
                variants = repository.list_product_variants(
                    principal.active_tenant_id,
                    product.id,
                )
            except ProductPageImportError as exc:
                results.append(ProductUrlImportItemResponse(
                    source_url=raw_url,
                    status="failed",
                    error_code=exc.code,
                    error_message=str(exc),
                ))
                continue
            except RrugcError as exc:
                results.append(ProductUrlImportItemResponse(
                    source_url=raw_url,
                    status="failed",
                    error_code=exc.code,
                    error_message=str(exc),
                ))
                continue

            primary_reference_imported = False
            variant_references_imported = 0
            warning: str | None = None
            if request.import_primary_image and not page.images:
                warning = "Product details were imported, but no product gallery image was found."
            elif request.import_primary_image and page.images:
                if not storage_available:
                    warning = (
                        "Product details were imported, but the primary image could not "
                        "be saved because Managed Drive is unavailable."
                    )
                else:
                    image_errors: list[str] = []
                    for image_url in page.images[:4]:
                        try:
                            content, _content_type = await fetch_product_image(
                                client, image_url
                            )
                            filename = (
                                urlsplit(image_url).path.rsplit("/", 1)[-1].strip()
                                or "product-primary"
                            )
                            await registry.upload_reference(
                                tenant_id=principal.active_tenant_id,
                                user_id=principal.user_id,
                                product_id=product.id,
                                view_type="front",
                                original_filename=filename[:255],
                                content=content,
                                storage=storage,
                            )
                            primary_reference_imported = True
                            break
                        except ProductPageImportError as exc:
                            image_errors.append(exc.code)
                        except RrugcError as exc:
                            image_errors.append(exc.code)
                    if not primary_reference_imported:
                        warning = (
                            "Product details were imported, but no gallery image could "
                            "be converted into a front reference."
                        )
                        if image_errors:
                            warning += " Last image error: " + image_errors[-1] + "."

            if request.import_primary_image and storage_available and variants:
                variant_errors = 0
                for variant in variants[:24]:
                    image_url = next(
                        (
                            str(value).strip()
                            for value in (variant.image_urls_json or [])
                            if str(value).strip()
                        ),
                        "",
                    )
                    if not image_url:
                        continue
                    try:
                        content, _content_type = await fetch_product_image(
                            client,
                            image_url,
                        )
                        filename = (
                            urlsplit(image_url).path.rsplit("/", 1)[-1].strip()
                            or ("variant-" + variant.source_variant_id)
                        )
                        await registry.upload_reference(
                            tenant_id=principal.active_tenant_id,
                            user_id=principal.user_id,
                            product_id=product.id,
                            variant_id=variant.id,
                            view_type="front",
                            original_filename=filename[:255],
                            content=content,
                            storage=storage,
                        )
                        variant_references_imported += 1
                    except (ProductPageImportError, RrugcError):
                        variant_errors += 1
                if variant_errors and not warning:
                    warning = (
                        f"{variant_errors} variant reference image"
                        + ("s" if variant_errors != 1 else "")
                        + " could not be imported."
                    )

            results.append(ProductUrlImportItemResponse(
                source_url=page.source_url,
                status="created" if created else "updated",
                product=_product(repository, product),
                images_found=len(page.images),
                variants_found=len(variants),
                variant_references_imported=variant_references_imported,
                primary_reference_imported=primary_reference_imported,
                warning=warning,
            ))

    return ProductUrlImportResponse(
        items=results,
        created=sum(item.status == "created" for item in results),
        updated=sum(item.status == "updated" for item in results),
        failed=sum(item.status == "failed" for item in results),
    )


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return _product(
        repository,
        _require_product(repository, principal.active_tenant_id, product_id),
    )


@router.patch("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: str,
    request: ProductUpdateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    product = _require_product(repository, principal.active_tenant_id, product_id)
    try:
        row = RrugcProductRegistry(session).update_product(
            product=product,
            request=request,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product(repository, row)


@router.patch(
    "/products/{product_id}/variants/{variant_id}",
    response_model=ProductVariantResponse,
)
def update_product_variant(
    product_id: str,
    variant_id: str,
    request: ProductVariantUpdateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    product = _require_product(
        repository,
        principal.active_tenant_id,
        product_id,
    )
    variant = repository.get_product_variant(
        principal.active_tenant_id,
        product_id,
        variant_id,
    )
    if variant is None:
        raise HTTPException(status_code=404, detail="Product variant not found")
    try:
        row = RrugcProductRegistry(session).set_variant_enabled(
            product=product,
            variant=variant,
            enabled=request.enabled,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    references = repository.list_product_references(
        principal.active_tenant_id,
        product_id,
    )
    return _product_variant(
        row,
        reference_count=sum(
            1 for reference in references
            if reference.variant_id == row.id
        ),
    )


@router.delete("/products/{product_id}", response_model=ProductResponse)
def archive_product(
    product_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    product = _require_product(repository, principal.active_tenant_id, product_id)
    row = RrugcProductRegistry(session).archive_product(product)
    return _product(repository, row)


@router.get(
    "/products/{product_id}/references",
    response_model=list[ProductReferenceResponse],
)
def list_product_references(
    product_id: str,
    include_archived: bool = Query(False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    return [
        _product_reference(row)
        for row in repository.list_product_references(
            principal.active_tenant_id,
            product_id,
            include_archived=include_archived,
        )
    ]


@router.get(
    "/products/{product_id}/references/{reference_id}/image",
)
async def get_product_reference_image(
    product_id: str,
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    reference = repository.get_product_reference(
        principal.active_tenant_id,
        product_id,
        reference_id,
    )
    if reference is None or not reference.remote_file_id:
        raise HTTPException(status_code=404, detail="Product reference image not found")

    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    try:
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=reference.id,
                remote_file_id=reference.remote_file_id,
                content_type=reference.content_type,
                size_bytes=reference.size_bytes,
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={
                "code": "product_reference_unavailable",
                "message": "Product reference image is unavailable.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post(
    "/products/{product_id}/references",
    response_model=ProductReferenceResponse,
    status_code=201,
)
async def upload_product_reference(
    product_id: str,
    view_type: ProductReferenceView = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    content = await _read_product_reference_upload(file)
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    try:
        row = await RrugcProductRegistry(session).upload_reference(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            product_id=product_id,
            view_type=view_type,
            original_filename=file.filename,
            content=content,
            storage=storage,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product_reference(row)


@router.delete(
    "/products/{product_id}/references/{reference_id}",
    response_model=ProductReferenceResponse,
)
def archive_product_reference(
    product_id: str,
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    reference = repository.get_product_reference(
        principal.active_tenant_id,
        product_id,
        reference_id,
    )
    if reference is None:
        raise HTTPException(status_code=404, detail="Product reference not found")
    row = RrugcProductRegistry(session).archive_reference(reference)
    return _product_reference(row)





@router.get(
    "/generation-capability",
    response_model=GenerationCapabilityResponse,
)
def generation_capability(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    return _rrugc_generation_capability(session, principal.active_tenant_id)


@router.get("/campaigns", response_model=list[CampaignResponse])
def list_campaigns(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return [_campaign(repository, row) for row in repository.list_campaigns(principal.active_tenant_id)]


@router.post("/campaigns", response_model=CampaignCreatedResponse, status_code=201)
def create_campaign(
    request: CampaignCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row, raw_token = RrugcService(session).create_campaign(
        tenant_id=principal.active_tenant_id,
        user_id=principal.user_id,
        **request.model_dump(),
    )
    response = _campaign(RrugcRepository(session), row).model_dump()
    return CampaignCreatedResponse(**response, scout_token=raw_token)


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return _campaign(
        repository,
        _require_campaign(repository, principal.active_tenant_id, campaign_id),
        include_keyword_health=True,
    )


@router.patch("/campaigns/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: str,
    request: CampaignUpdateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(repository, principal.active_tenant_id, campaign_id)
    try:
        row = RrugcService(session).update_campaign(
            campaign,
            **request.model_dump(exclude_unset=True),
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _campaign(repository, row)


@router.delete("/campaigns/{campaign_id}", status_code=204)
def archive_campaign(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(repository, principal.active_tenant_id, campaign_id)
    RrugcService(session).archive_campaign(campaign)
    return Response(status_code=204)


@router.put(
    "/campaigns/{campaign_id}/scout-automation",
    response_model=CampaignResponse,
)
def configure_campaign_scout_automation(
    campaign_id: str,
    request: CampaignScoutAutomationRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcAutoScoutService(session).configure_campaign(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
            auto_scout=request.auto_scout,
            scan_interval_seconds=request.scan_interval_seconds,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _campaign(RrugcRepository(session), row)


@router.put(
    "/campaigns/{campaign_id}/product",
    response_model=CampaignResponse,
)
def bind_campaign_product(
    campaign_id: str,
    request: CampaignProductBindRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository, principal.active_tenant_id, campaign_id
    )
    try:
        row = RrugcGenerationFoundation(session).bind_campaign_product(
            campaign=campaign,
            product_id=request.product_id,
            variant_ids=request.variant_ids,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _campaign(repository, row)


@router.get(
    "/campaigns/{campaign_id}/generation-attempts",
    response_model=list[GenerationAttemptResponse],
)
def list_generation_attempts(
    campaign_id: str,
    candidate_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    return [
        _generation_attempt(row)
        for row in repository.list_generation_attempts(
            principal.active_tenant_id,
            campaign_id,
            candidate_id=candidate_id,
            limit=limit,
        )
    ]


@router.get(
    "/campaigns/{campaign_id}/supervisor-results",
    response_model=list[SupervisorResultResponse],
)
def list_supervisor_results(
    campaign_id: str,
    generation_attempt_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    return [
        _supervisor_result(repository, row)
        for row in repository.list_supervisor_results(
            principal.active_tenant_id,
            campaign_id,
            generation_attempt_id=generation_attempt_id,
            limit=limit,
        )
    ]


@router.post(
    "/supervisor-results/{result_id}/prepare-correction",
    response_model=GenerationAttemptCreatedResponse,
    status_code=201,
)
def prepare_supervisor_correction(
    result_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    result = repository.get_supervisor_result(
        principal.active_tenant_id, result_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Supervisor result not found")
    try:
        row, created = RrugcSupervisorService(session).prepare_correction(
            result=result,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return GenerationAttemptCreatedResponse(
        created=created,
        attempt=_generation_attempt(row),
    )


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/generation-attempts",
    response_model=GenerationAttemptCreatedResponse,
    status_code=201,
)
def prepare_generation_attempt(
    campaign_id: str,
    candidate_id: str,
    request: GenerationAttemptCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository, principal.active_tenant_id, campaign_id
    )
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row, created = RrugcGenerationFoundation(session).prepare_attempt(
            campaign=campaign,
            candidate=candidate,
            user_id=principal.user_id,
            generation_variant=request.generation_variant,
            worker_skill_version=request.worker_skill_version,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return GenerationAttemptCreatedResponse(
        created=created,
        attempt=_generation_attempt(row),
    )


@router.post(
    "/generation-attempts/{attempt_id}/execute",
    response_model=GenerationAttemptResponse,
    status_code=202,
)
def execute_generation_attempt(
    attempt_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    attempt = repository.get_generation_attempt(
        principal.active_tenant_id, attempt_id
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Generation attempt not found")
    capability = _rrugc_generation_capability(
        session, principal.active_tenant_id
    )
    if not capability.available:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rrugc_generation_provider_unavailable",
                "message": capability.reason
                or "Reference-conditioned generation is unavailable.",
            },
        )
    try:
        row, _created = RrugcGenerationFoundation(session).enqueue_attempt(
            attempt=attempt,
            actor_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _generation_attempt(row)


@router.get(
    "/review-tasks",
    response_model=ReviewTaskListResponse,
)
def list_review_tasks(
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    if status is not None and status not in {"pending", "approved", "rejected"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "rrugc_review_status_invalid"},
        )
    if priority is not None and priority not in {"standard", "high"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "rrugc_review_priority_invalid"},
        )
    repository = RrugcRepository(session)
    rows, total = repository.list_review_tasks(
        principal.active_tenant_id,
        status=status,
        priority=priority,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
    return ReviewTaskListResponse(
        items=[_review_task(repository, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/review-tasks/{task_id}",
    response_model=ReviewTaskResponse,
)
def get_review_task(
    task_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    row = repository.get_review_task(principal.active_tenant_id, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    return _review_task(repository, row)


@router.post(
    "/review-tasks/{task_id}/approve",
    response_model=ReviewTaskTransitionResponse,
)
def approve_review_task(
    task_id: str,
    request: ReviewTaskDecisionRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, transitioned = RrugcReviewService(session).transition(
            tenant_id=principal.active_tenant_id,
            task_id=task_id,
            target_status="approved",
            user_id=principal.user_id,
            review_note=request.review_note,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return ReviewTaskTransitionResponse(
        transitioned=transitioned,
        task=_review_task(RrugcRepository(session), row),
    )


@router.post(
    "/review-tasks/{task_id}/reject",
    response_model=ReviewTaskTransitionResponse,
)
def reject_review_task(
    task_id: str,
    request: ReviewTaskDecisionRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, transitioned = RrugcReviewService(session).transition(
            tenant_id=principal.active_tenant_id,
            task_id=task_id,
            target_status="rejected",
            user_id=principal.user_id,
            review_note=request.review_note,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return ReviewTaskTransitionResponse(
        transitioned=transitioned,
        task=_review_task(RrugcRepository(session), row),
    )


@router.post(
    "/review-tasks/reconcile",
    response_model=ReviewTaskReconcileResponse,
)
def reconcile_review_tasks(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        result = RrugcReviewService(session).reconcile(
            tenant_id=principal.active_tenant_id,
            limit=limit,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return ReviewTaskReconcileResponse(
        scanned=result.scanned,
        created=result.created,
    )


@router.get(
    "/exports",
    response_model=ExportListResponse,
)
def list_exports(
    campaign_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    rows, total = repository.list_exports(
        principal.active_tenant_id,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
    return ExportListResponse(
        items=[_export(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/generation-attempts/{attempt_id}/export",
    response_model=ExportResponse,
)
def export_generation_attempt(
    attempt_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, _created = RrugcExportService(session).export_attempt(
            tenant_id=principal.active_tenant_id,
            generation_attempt_id=attempt_id,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _export(row)


@router.post(
    "/campaigns/{campaign_id}/exports",
    response_model=BatchExportResponse,
)
def export_campaign_outputs(
    campaign_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        result = RrugcExportService(session).export_campaign(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
            user_id=principal.user_id,
            limit=limit,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return BatchExportResponse(
        scanned=result.scanned,
        exported=result.exported,
        reused=result.reused,
        items=[_export(row) for row in result.items],
    )


@router.get(
    "/campaigns/{campaign_id}/export-summary",
    response_model=CampaignExportSummaryResponse,
)
def get_campaign_export_summary(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        summary = RrugcExportService(session).summary(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return CampaignExportSummaryResponse(**summary)


@router.get(
    "/delivery-destinations",
    response_model=list[DeliveryDestinationResponse],
)
def list_delivery_destinations(
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    service = RrugcDeliveryService(session)
    return [
        _delivery_destination(row)
        for row in service.list_destinations(
            tenant_id=principal.active_tenant_id,
            include_archived=include_archived,
        )
    ]


@router.post(
    "/delivery-destinations",
    response_model=DeliveryDestinationResponse,
    status_code=201,
)
def create_delivery_destination(
    request: DeliveryDestinationCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcDeliveryService(session).create_destination(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            **request.model_dump(),
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _delivery_destination(row)


@router.delete(
    "/delivery-destinations/{destination_id}",
    response_model=DeliveryDestinationResponse,
)
def archive_delivery_destination(
    destination_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcDeliveryService(session).archive_destination(
            tenant_id=principal.active_tenant_id,
            destination_id=destination_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _delivery_destination(row)


@router.put(
    "/campaigns/{campaign_id}/lifecycle-policy",
    response_model=CampaignResponse,
)
def update_campaign_lifecycle_policy(
    campaign_id: str,
    request: CampaignLifecyclePolicyRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcDeliveryService(session).set_campaign_policy(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
            auto_complete_on_delivery=request.auto_complete_on_delivery,
            completion_destination_id=request.completion_destination_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _campaign(RrugcRepository(session), row)


@router.get(
    "/delivery-packages",
    response_model=DeliveryPackageListResponse,
)
def list_delivery_packages(
    campaign_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    service = RrugcDeliveryService(session)
    rows, total = service.list_packages(
        tenant_id=principal.active_tenant_id,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
    return DeliveryPackageListResponse(
        items=[_delivery_package(service, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/campaigns/{campaign_id}/deliveries/{destination_id}",
    response_model=DeliveryPackageResponse,
)
async def deliver_campaign(
    campaign_id: str,
    destination_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    storage = build_managed_storage_provider(get_settings())
    if not isinstance(storage, GoogleDriveAssetStorage):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    service = RrugcDeliveryService(session, storage=storage)
    try:
        row = await service.deliver_campaign(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
            destination_id=destination_id,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _delivery_package(service, row)


@router.get(
    "/campaigns/{campaign_id}/delivery-summary",
    response_model=CampaignDeliverySummaryResponse,
)
def get_campaign_delivery_summary(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        summary = RrugcDeliveryService(session).delivery_summary(
            tenant_id=principal.active_tenant_id,
            campaign_id=campaign_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return CampaignDeliverySummaryResponse(**summary)


@router.post(
    "/delivery-lifecycle/reconcile",
    response_model=DeliveryLifecycleReconcileResponse,
)
def reconcile_delivery_lifecycle(
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    result = RrugcDeliveryService(session).reconcile_lifecycle(
        tenant_id=principal.active_tenant_id,
        limit=limit,
    )
    return DeliveryLifecycleReconcileResponse(
        scanned=result.scanned,
        expired=result.expired,
    )


@router.get(
    "/delivery-operations/summary",
    response_model=DeliveryOperationsSummaryResponse,
)
def get_delivery_operations_summary(
    event_limit: int = Query(default=12, ge=1, le=50),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    settings = get_settings()
    service = RrugcDeliveryService(session, settings=settings)
    summary = service.operations_summary(
        tenant_id=principal.active_tenant_id,
    )
    events = service.recent_events(
        tenant_id=principal.active_tenant_id,
        limit=event_limit,
    )
    return DeliveryOperationsSummaryResponse(
        **summary,
        recent_events=[_delivery_event(row) for row in events],
    )


@router.post(
    "/delivery-operations/maintenance",
    response_model=DeliveryMaintenanceEnqueueResponse,
)
def enqueue_delivery_maintenance(
    principal: CurrentPrincipal = Depends(RUN),
):
    settings = get_settings()
    if not (
        settings.RRUGC_DELIVERY_AUTOMATION_ENABLED
        and settings.PROCESSING_JOBS_ENABLED
        and settings.MANAGED_ASSET_STORAGE_ENABLED
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rrugc_delivery_automation_disabled",
                "message": "RRUGC delivery automation is disabled.",
            },
        )
    result = RrugcDeliveryMaintenanceScheduler(
        SessionLocal,
        settings,
    ).enqueue_tenant(
        principal.active_tenant_id,
        force=True,
    )
    return DeliveryMaintenanceEnqueueResponse(
        created=result.created,
        job_id=result.job_id,
    )


@router.get("/generation-attempts/{attempt_id}/output")
async def get_generation_attempt_output(
    attempt_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    attempt = repository.get_generation_attempt(
        principal.active_tenant_id, attempt_id
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Generation attempt not found")
    if attempt.status != "completed" or not attempt.output_remote_file_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rrugc_generation_output_not_ready",
                "message": "Generated output is not ready.",
            },
        )
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    try:
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=f"rrugc-generation:{attempt.id}",
                remote_file_id=attempt.output_remote_file_id,
                content_type=attempt.output_content_type,
                size_bytes=attempt.output_size_bytes,
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 502,
            detail={
                "code": exc.code,
                "message": "Generated output could not be opened.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=attempt.output_content_type or stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )



@router.get("/campaigns/{campaign_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(
    campaign_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    return [
        _candidate(row)
        for row in repository.list_candidates(
            principal.active_tenant_id, campaign_id, limit=limit, offset=offset
        )
    ]


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/analyze",
    response_model=AnalyzeResponse,
    status_code=202,
)
def analyze_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).reanalyze_candidate(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return AnalyzeResponse(candidate=_candidate(row))


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/ai-feedback",
    response_model=CandidateAiFeedbackResponse,
)
def mark_candidate_ai_feedback(
    campaign_id: str,
    candidate_id: str,
    body: CandidateAiFeedbackRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).mark_candidate_ai_label(
            candidate,
            label=body.label,
            note=body.note,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    calibration = build_ai_risk_calibration(
        repository.ai_feedback_training_rows(principal.active_tenant_id)
    )
    return CandidateAiFeedbackResponse(
        candidate=_candidate(row),
        calibration=AiFeedbackCalibrationResponse(
            active=calibration.active,
            real_count=calibration.real_count,
            ai_count=calibration.ai_count,
            real_mean=calibration.real_mean,
            ai_mean=calibration.ai_mean,
        ),
    )


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
    response_model=CandidateReferenceFeedbackResponse,
)
def mark_candidate_reference_feedback(
    campaign_id: str,
    candidate_id: str,
    body: CandidateReferenceFeedbackRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).mark_candidate_reference_label(
            candidate,
            label=body.label,
            note=body.note,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    learning_intent = campaign_learning_intent(
        campaign_id=campaign.id,
        name=campaign.name,
        queries=list(campaign.search_queries_json or [campaign.query]),
        product_snapshot=campaign.product_snapshot_json,
    )
    learning = build_reference_preference_model(
        repository.reference_feedback_training_rows(
            principal.active_tenant_id,
            intent=learning_intent,
            legacy_campaign_id=campaign.id,
        )
    )
    return CandidateReferenceFeedbackResponse(
        candidate=_candidate(row),
        learning=ReferencePreferenceLearningResponse(
            active=learning.active,
            good_count=learning.good_count,
            bad_count=learning.bad_count,
        ),
    )


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/import",
    response_model=ImportResponse,
    status_code=202,
)
def import_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).queue_candidate_import(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return ImportResponse(candidate=_candidate(row))


@router.get(
    "/scout-agents",
    response_model=list[ScoutAgentResponse],
)
def list_scout_agents(
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    service = RrugcAutoScoutService(session)
    return [
        _scout_agent_response(row)
        for row in service.list_agents(
            tenant_id=principal.active_tenant_id,
            include_archived=include_archived,
        )
    ]


@router.post(
    "/scout-agents",
    response_model=ScoutAgentCreatedResponse,
    status_code=201,
)
def create_scout_agent(
    request: ScoutAgentCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, raw_token = RrugcAutoScoutService(session).create_agent(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            name=request.name,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    payload = _scout_agent_response(row).model_dump()
    return ScoutAgentCreatedResponse(**payload, agent_token=raw_token)


@router.delete(
    "/scout-agents/{agent_id}",
    response_model=ScoutAgentResponse,
)
def archive_scout_agent(
    agent_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcAutoScoutService(session).archive_agent(
            tenant_id=principal.active_tenant_id,
            agent_id=agent_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _scout_agent_response(row)


@router.get(
    "/scout-runs",
    response_model=list[ScoutRunResponse],
)
def list_scout_runs(
    campaign_id: str | None = Query(default=None, max_length=36),
    agent_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    rows = RrugcRepository(session).list_scout_runs(
        principal.active_tenant_id,
        campaign_id=campaign_id,
        agent_id=agent_id,
        limit=limit,
    )
    return [_scout_run_response(row) for row in rows]


@router.post(
    "/scout-agents/{agent_id}/heartbeat",
    response_model=ScoutAgentResponse,
)
def auto_scout_agent_heartbeat(
    agent_id: str,
    request: ScoutAgentHeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    service = RrugcAutoScoutService(session)
    token = _bearer_token(authorization)
    try:
        row = service.heartbeat(
            agent_id=agent_id,
            raw_token=token,
            status=request.status,
            client_version=request.client_version,
            machine_label=request.machine_label,
            run_id=request.run_id,
            error_code=request.error_code,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _scout_agent_response(row)


@router.get(
    "/scout-agents/{agent_id}/diagnostics",
)
def auto_scout_agent_diagnostics(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    service = RrugcAutoScoutService(session)
    token = _bearer_token(authorization)
    try:
        return service.diagnostics(
            agent_id=agent_id,
            raw_token=token,
        )
    except RrugcError as exc:
        raise _error(exc) from exc


@router.post(
    "/scout-agents/{agent_id}/claim",
    response_model=ScoutClaimResponse | None,
)
def auto_scout_agent_claim(
    agent_id: str,
    authorization: str | None = Header(default=None),
    x_scout_version: str | None = Header(default=None, alias="X-Scout-Version"),
    x_scout_machine: str | None = Header(default=None, alias="X-Scout-Machine"),
    session: Session = Depends(get_db),
):
    service = RrugcAutoScoutService(session)
    token = _bearer_token(authorization)
    try:
        claim = service.claim(
            agent_id=agent_id,
            raw_token=token,
            client_version=x_scout_version,
            machine_label=x_scout_machine,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    if claim is None:
        return None
    search_queries = list(claim.search_queries)
    return ScoutClaimResponse(
        run=_scout_run_response(claim.run),
        campaign_id=claim.campaign.id,
        query=claim.campaign.query,
        search_queries=search_queries,
        target_count=claim.campaign.target_count,
        max_scroll_batches=claim.campaign.max_scroll_batches,
        auto_import=claim.campaign.auto_import,
        progress=claim.progress,
        pipeline_count=claim.pipeline_count,
    )


@router.post(
    "/scout-agents/{agent_id}/runs/{run_id}/candidates",
    response_model=AutoScoutCandidateBatchResponse,
)
def auto_scout_run_candidates(
    agent_id: str,
    run_id: str,
    request: CandidateBatchRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    service = RrugcAutoScoutService(session)
    token = _bearer_token(authorization)
    try:
        result = service.submit_candidates(
            agent_id=agent_id,
            raw_token=token,
            run_id=run_id,
            submissions=request.items,
            source_query=request.source_query,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return AutoScoutCandidateBatchResponse(
        created=result.created,
        existing=result.existing,
        progress=result.progress,
        pipeline_count=result.pipeline_count,
        target_count=result.target_count,
        campaign_status=result.campaign_status,
    )


@router.post(
    "/scout-agents/{agent_id}/runs/{run_id}/complete",
    response_model=ScoutRunResponse,
)
def auto_scout_run_complete(
    agent_id: str,
    run_id: str,
    request: ScoutRunCompleteRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    service = RrugcAutoScoutService(session)
    token = _bearer_token(authorization)
    try:
        row = service.complete(
            agent_id=agent_id,
            raw_token=token,
            run_id=run_id,
            status=request.status,
            error_code=request.error_code,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _scout_run_response(row)


@router.get("/scout/{campaign_id}/task", response_model=ScoutTaskResponse)
def scout_task(
    campaign_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    repository = RrugcRepository(session)
    counts = repository.campaign_counts(row.tenant_id, row.id)
    row.scout_last_seen_at = datetime.now(timezone.utc)
    row.scout_status = "ready"
    session.commit()
    return ScoutTaskResponse(
        campaign_id=row.id,
        query=row.query,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        status=row.status,
        discovered=sum(counts.values()),
        approved=_count_sum(counts, ANALYSIS_APPROVED_STATUSES),
        rejected=_count_sum(counts, ANALYSIS_REJECTED_STATUSES),
        analysis_pending=counts.get("analysis_queued", 0) + counts.get("analyzing", 0),
        drive_ready=counts.get("drive_ready", 0),
    )


@router.post("/scout/{campaign_id}/heartbeat", status_code=204)
def scout_heartbeat(
    campaign_id: str,
    request: ScoutHeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    row.scout_status = request.status
    row.scout_last_seen_at = datetime.now(timezone.utc)
    session.commit()
    return None


@router.post(
    "/scout/{campaign_id}/candidates",
    response_model=CandidateBatchResponse,
)
def scout_candidates(
    campaign_id: str,
    request: CandidateBatchRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    if row.status != "running":
        raise HTTPException(status_code=409, detail="Campaign is not running")
    try:
        candidates, created, existing = RrugcService(session).ingest_candidates(
            campaign=row,
            submissions=request.items,
        )
    except RrugcError as exc:
        raise _error(exc) from exc

    return CandidateBatchResponse(
        created=created,
        existing=existing,
        items=[_candidate(candidate) for candidate in candidates],
    )
