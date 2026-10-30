from __future__ import annotations

import asyncio
import base64
import binascii
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.domain.providers.contracts import (
    AiProviderError,
    OpenStoredAssetInput,
    StorageProviderError,
)
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository
from app.modules.explorer.cache import CachedThumbnail, thumbnail_cache
from app.modules.realistic_review_ugc.output_image_sanitizer import (
    MAX_PREVIEW_BYTES, stage1_png_thumbnail_bytes,
)
from app.modules.realistic_review_ugc.stage1_fabric import thread_preview_bytes
from app.modules.realistic_review_ugc.image_cache_policy import (
    matches_etag,
    preview_cache_control,
    preview_etag,
)
from app.modules.image_generation.providers import GEMINI_IMAGE_MODEL
from app.modules.image_generation.service import provider_capability
from app.providers.ai.codex_execution_log import read_codex_execution_log
from app.providers.ai.codex_image import (
    CodexImageGenRunner,
    CodexImageProviderError,
    CodexImageRunnerConfig,
    CodexSkillManifest,
    list_codex_skill_manifests,
    load_codex_skill_manifest,
    recommend_codex_skill,
)
from app.modules.realistic_review_ugc.analysis import (
    build_ai_risk_calibration,
    build_reference_preference_model,
)
from app.modules.realistic_review_ugc.keyword_strategy import campaign_learning_intent
from app.modules.realistic_review_ugc.keyword_volume import (
    KeywordVolumeError,
    RrugcKeywordVolumeService,
    trademark_evidence_is_current,
)
from app.modules.realistic_review_ugc.quote_scout_analysis import (
    QuoteScoutError,
    analyze_hat_quote,
)
from app.modules.realistic_review_ugc.generation import (
    RrugcGenerationFoundation,
    binding_is_generation_ready,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcGenerationAttemptModel,
    RrugcKeywordVolumeModel,
    RrugcKeywordAgentOriginModel,
    RrugcScoutFeedbackModel,
    RrugcScoutMetricCycleModel,
    RrugcStage2JobModel,
    RrugcImageOutputVersionModel,
    RrugcKeywordImageJobModel,
    RrugcStage2SkillRegistryModel,
    RrugcStage3AnalysisModel,
    RrugcSupervisorResultModel,
    RrugcReviewTaskModel,
    RrugcExportModel,
    RrugcScoutAgentModel,
    RrugcScoutRunModel,
    RrugcSourcePlanModel,
    RrugcDeliveryDestinationModel,
    RrugcDeliveryPackageModel,
    RrugcDeliveryEventModel,
    RrugcDeliveryItemModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
    RrugcProductVariantModel,
    RrugcReferenceAssetModel,
    RrugcReferenceSetModel,
    RrugcReferenceSetItemModel,
    RrugcReferenceSeedModel,
)
from app.modules.realistic_review_ugc.product_context import (
    analyze_product_visual_reference,
    context_feedback_ranking_signal,
    merge_product_visual_context,
    product_visual_binding_fingerprint,
    select_product_visual_references,
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
from app.modules.realistic_review_ugc.maintenance import RrugcMaintenanceService
from app.modules.realistic_review_ugc.seed_similarity import seed_visual_ranking_signal
from app.modules.realistic_review_ugc.reference_library import (
    REFERENCE_ASSET_MAX_BYTES,
    ReferenceLibraryError,
    RrugcReferenceLibrary,
    reference_image_content_type,
)
from app.modules.realistic_review_ugc.reference_recommendations import (
    build_reference_review_learning,
    discouraged_reference_sets,
    recommend_reference_assets,
    recommend_reference_set_reuse,
    suggested_reference_set_name,
)
from app.modules.realistic_review_ugc.reference_sets import (
    ReferenceSetError,
    RrugcReferenceSetService,
    normalize_reference_role,
)
from app.modules.realistic_review_ugc.schema import (
    AnalyzeResponse,
    CampaignCreatedResponse,
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignProductBindRequest,
    CampaignResponse,
    CampaignScoutAutomationRequest,
    CandidateBatchRequest,
    AutoScoutCandidateBatchRequest,
    AutoScoutCandidateBatchResponse,
    CandidateBatchResponse,
    CandidateResponse,
    CandidateAiFeedbackRequest,
    CandidateAiFeedbackResponse,
    AiFeedbackCalibrationResponse,
    CandidateReferenceFeedbackRequest,
    CandidateReferenceFeedbackResponse,
    CandidateContextFeedbackRequest,
    CandidateContextFeedbackResponse,
    ReferencePreferenceLearningResponse,
    GenerationAttemptCreateRequest,
    GenerationAttemptCreatedResponse,
    GenerationAttemptResponse,
    GenerationCapabilityResponse,
    GenerationSkillCatalogResponse,
    GenerationSkillResponse,
    ImportResponse,
    KeywordVolumeOverviewResponse,
    KeywordVolumePageResponse,
    ScoutKeywordSummaryResponse,
    KeywordVolumePickRequest,
    KeywordVolumeFavoriteRequest,
    ScoutFeedbackRequest,
    ScoutFeedbackFinishRequest,
    ScoutFeedbackLeaseRequest,
    ScoutMetricCycleRequest,
    ScoutQueryCompleteRequest,
    KeywordSuggestionResponse,
    KeywordVolumeResolveRequest,
    KeywordVolumeResolveResponse,
    KeywordVolumeResponse,
    KeywordVolumeTrendPointResponse,
    QuoteScoutAnalyzeRequest,
    QuoteScoutAnalyzeResponse,
    ReferenceAssetResponse,
    ReferenceAssetPromotionResponse,
    ReferenceSeedRequest,
    ReferenceSeedResponse,
    ReferenceSetCreateRequest,
    ReferenceSetSkillPresetCreateRequest,
    ReferenceSetRecommendationItemResponse,
    ReferenceSetRecommendationResponse,
    ReferenceSetReuseRecommendationResponse,
    ReferenceSetFeedbackResponse,
    ReferenceSetItemCreateRequest,
    ReferenceSetItemResponse,
    ReferenceSetResponse,
    ReferenceSetItemBindingResponse,
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
    RrugcHealthResponse,
    ScoutAgentHeartbeatRequest,
    ScoutLogBatchRequest,
    ScoutLogBatchResponse,
    ScoutClaimResponse,
    ScoutQueryPerformance,
    ScoutRunCompleteRequest,
    ScoutRunResponse,
    ScoutRelatedSeed,
    ScoutHeartbeatRequest,
    ScoutTaskResponse,
    SourcePlanReferencePreviewResponse,
    SourcePlanGroupImageResponse,
    SourcePlanResponse,
    SourcePlanOverviewResponse,
    SourcePlanPageResponse,
    SourcePlanSyncResponse,
    Stage2SkillCatalogResponse,
    Stage2SkillResponse,
    Stage2SkillSyncRequest,
    Stage2SkillRegistryItemResponse,
    Stage2SkillRegistryResponse,
    Stage2SkillEnabledRequest,
    Stage2SkillNoteRequest,
    StageSkillDefaultRequest,
    Stage2SkillDefaultVersionRequest,
    Stage2JobCreateRequest,
    KeywordImageCreateRequest,
    KeywordImageManualCreateRequest,
    ColorwayBatchRequest,
    KeywordImagePageResponse,
    KeywordImageRowResponse,
    Stage2JobCreatedResponse,
    Stage2JobResponse,
    Stage2JobsCancelRequest,
    Stage2JobsCancelledResponse,
    Stage3AnalyzeRequest,
    Stage3AnalyzeResponse,
    Stage3ReviewGroupListResponse,
    Stage3ReviewFolderCountResponse,
    Stage3ReviewSummaryResponse,
    Stage3ReviewGroupResponse,
    Stage3ReviewImageResponse,
)
from app.modules.realistic_review_ugc.scout_dashboard import scout_jobs_snapshot
from app.modules.realistic_review_ugc.query_intelligence import (
    claim_query, renew_query, finish_query, intelligence_summary,
)
from app.modules.realistic_review_ugc.scout_feedback import (
    update_feedback, statuses_for_rows, feedback_targets, blocked_targets,
    claim_priority, finish_priority, renew_priority,
)
from app.modules.realistic_review_ugc.review import RrugcReviewService
from app.modules.realistic_review_ugc.scout_automation import (
    RrugcAutoScoutService,
    effective_agent_status,
    keyword_health_rows,
    quality_pipeline_count,
    scout_analysis_backpressure,
    review_scout_soft_throttle,
    keyword_quote_backlog_gate,
)
from app.modules.realistic_review_ugc.scout_log import (
    RrugcScoutLogService,
    SCOUT_LOG_RETENTION_DAYS,
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
    validate_image_url,
    validate_pin_url,
)
from app.modules.realistic_review_ugc.stage2 import (
    RrugcStage2Error,
    RrugcStage2Service,
    STAGE2_CANCEL_GRACE_SECONDS,
)
from app.modules.realistic_review_ugc.keyword_images import KeywordImageService, KeywordImageError, effective_status
from app.modules.realistic_review_ugc.colorways import ColorwayService, ColorwayError
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel
from app.modules.realistic_review_ugc.stage3 import RrugcStage3Service, STAGE3_ANALYSIS_VERSION
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillItem,
    Stage2SkillRegistryError,
    list_stage2_skill_catalog,
    export_installed_stage2_skill_bundle,
)
from app.modules.realistic_review_ugc.skill_registry import (
    create_skill,
    create_skill_version,
    delete_skill,
    delete_skill_version,
    enabled_catalog_items,
    ensure_skill_registry,
    get_registry_row_by_skill_id,
    registry_payload,
    restore_keyword_skill,
    update_local_keyword_artwork_skill,
    set_skill_default,
    set_skill_enabled,
    set_skill_note,
    sync_skill,
)
from app.modules.realistic_review_ugc.stage_skill_settings import (
    stage_skill_defaults, set_stage_skill_default, keyword_skill_compatible,
)
from app.modules.realistic_review_ugc.output_versions import output_versions
from app.modules.realistic_review_ugc.output_downloads import (
    OutputArchiveError, archive_filename, build_output_archive, output_download_name,
)
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    RRUGC_SOURCE_TARGET_COUNT,
    RrugcSourcePlanError,
    embroidery_signature,
    sync_source_plans,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.google.drive import close_thumbnail_stream, open_thumbnail_stream
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


router = APIRouter(
    prefix="/api/v1/realistic-review-ugc",
    tags=["realistic-review-ugc"],
)
from .products_video_stage import router as products_stage3_video_router
from .cap_stack_router import router as cap_stack_router
from .hat_stock_router import router as hat_stock_router
from .creative_brief_router import router as creative_brief_router
from .typography_router import router as typography_router, status_router as typography_status_router
router.include_router(typography_status_router)
router.include_router(typography_router)
router.include_router(products_stage3_video_router)
router.include_router(cap_stack_router)
router.include_router(hat_stock_router)
router.include_router(creative_brief_router)
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")


def _can_manage_stage2_skills(principal: CurrentPrincipal) -> bool:
    """Skills belong to RRUGC, not to tenant-wide role administration.

    Viewer/operator roles already receive realistic_review_ugc.configure in the
    tenant RBAC seed. Honor this scoped capability consistently in both the
    registry's can_manage response and all Skill mutation endpoints.
    """
    return bool(
        principal.platform_admin
        or "realistic_review_ugc.configure" in principal.effective_permissions
    )


def _require_stage2_skill_admin(principal: CurrentPrincipal) -> None:
    if _can_manage_stage2_skills(principal):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "rrugc_configure_permission_required",
            "message": "Realistic Review UGC configure permission is required to manage workflow Skills.",
            "required_permission": "realistic_review_ugc.configure",
        },
    )


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


def _reference_library_error(exc: ReferenceLibraryError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _reference_set_error(exc: ReferenceSetError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _ranking_product_context(
    campaign: RrugcCampaignModel | None,
) -> dict | None:
    if campaign is None or campaign.discovery_mode != "product_context":
        return None
    return (
        dict(campaign.product_context_json)
        if isinstance(campaign.product_context_json, dict)
        else None
    )


def _candidate(
    row: RrugcCandidateModel,
    *,
    product_context: dict | None = None,
) -> CandidateResponse:
    signal = row.ai_signal_json if isinstance(row.ai_signal_json, dict) else {}
    context_match = (
        dict(signal.get("context_match"))
        if isinstance(signal.get("context_match"), dict)
        else {}
    )
    ranking = context_feedback_ranking_signal(
        profile=product_context,
        source_query=signal.get("scout_query"),
        base_score=row.final_score,
    )
    seed_ranking = seed_visual_ranking_signal(
        signal=signal,
        base_score=ranking["ranking_score"],
    )
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
        "context_manual_label": signal.get("context_manual_label"),
        "context_manual_note": signal.get("context_manual_note"),
        "context_manual_reviewed_by_user_id": signal.get("context_manual_reviewed_by_user_id"),
        "context_manual_reviewed_at": signal.get("context_manual_reviewed_at"),
        "product_fit_score": row.product_fit_score,
        "context_match_active": bool(context_match.get("active")),
        "context_match_score": context_match.get("score"),
        "context_match_evidence": list(context_match.get("evidence") or []),
        "matched_variant_id": row.matched_variant_id,
        "matched_variant_name": row.matched_variant_name,
        "matched_color": row.matched_color,
        "color_match_score": row.color_match_score,
        "product_shape_score": row.product_shape_score,
        "final_score": row.final_score,
        "ranking_score": seed_ranking["ranking_score"],
        "source_query": ranking["source_query"],
        "context_feedback_adjustment": ranking["adjustment"],
        "context_feedback_direction": ranking["direction"],
        "context_feedback_reviews": ranking["reviews"],
        "seed_visual_active": seed_ranking["active"],
        "seed_visual_score": seed_ranking["score"],
        "seed_visual_adjustment": seed_ranking["adjustment"],
        "seed_visual_positive_similarity": seed_ranking["positive_similarity"],
        "seed_visual_negative_similarity": seed_ranking["negative_similarity"],
        "seed_visual_positive_count": seed_ranking["positive_count"],
        "seed_visual_negative_count": seed_ranking["negative_count"],
        "seed_visual_profile_key": seed_ranking["profile_key"],
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


def _reference_asset(row: RrugcReferenceAssetModel) -> ReferenceAssetResponse:
    return ReferenceAssetResponse(
        id=row.id,
        source_type=row.source_type,
        source_key=row.source_key,
        source_url=row.source_url,
        original_filename=row.original_filename,
        source_campaign_id=row.source_campaign_id,
        source_candidate_id=row.source_candidate_id,
        profile_key=row.profile_key,
        reference_type=row.reference_type,
        status=row.status,
        content_hash=row.content_hash,
        width=row.width,
        height=row.height,
        size_bytes=row.size_bytes,
        image_format=row.image_format,
        tags=list(row.tags_json or []),
        themes=list(row.themes_json or []),
        quality_score=row.quality_score,
        visual_score=row.visual_score,
        context_score=row.context_score,
        usage_count=int(row.usage_count or 0),
        remote_file_id=row.remote_file_id,
        remote_folder_id=row.remote_folder_id,
        web_url=row.web_url,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _reference_seed(row: RrugcReferenceSeedModel) -> ReferenceSeedResponse:
    return ReferenceSeedResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        reference_asset_id=row.reference_asset_id,
        profile_key=row.profile_key,
        label=row.label,
        note=row.note,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _reference_set_item(
    row: RrugcReferenceSetItemModel,
) -> ReferenceSetItemResponse:
    return ReferenceSetItemResponse(
        id=row.id,
        reference_asset_id=row.reference_asset_id,
        role=row.role,
        position=row.position,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _reference_set(
    repository: RrugcRepository,
    row: RrugcReferenceSetModel,
) -> ReferenceSetResponse:
    return ReferenceSetResponse(
        id=row.id,
        name=row.name,
        campaign_id=row.campaign_id,
        profile_key=row.profile_key,
        description=row.description,
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
        items=[
            _reference_set_item(item)
            for item in repository.list_reference_set_items(
                row.tenant_id,
                row.id,
            )
        ],
    )


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


async def _read_reference_asset_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > REFERENCE_ASSET_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "reference_asset_too_large",
                    "message": "Reference image exceeds the 20 MB limit.",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _count_sum(counts: Counter, statuses: set[str]) -> int:
    return sum(int(counts.get(status, 0)) for status in statuses)




def _campaign_generation_product_type(
    campaign: RrugcCampaignModel | None,
) -> str | None:
    if campaign is None:
        return None
    snapshot = dict(campaign.product_snapshot_json or {})
    return str(snapshot.get("product_type") or "").strip() or None


def _codex_generation_skill_catalog(
    campaign: RrugcCampaignModel | None = None,
) -> tuple[list[CodexSkillManifest], CodexSkillManifest | None]:
    settings = get_settings()
    codex_home = str(
        getattr(
            settings,
            "CODEX_IMAGE_HOME",
            "/var/lib/creative-asset-manager/codex",
        )
    )
    manifests = list_codex_skill_manifests(
        codex_home,
        workflow="rrugc_generate",
    )
    fallback_skill = (
        str(getattr(settings, "CODEX_IMAGE_SKILL", "worker-hat-v1")).strip()
        or "worker-hat-v1"
    )
    recommended = recommend_codex_skill(
        manifests,
        product_type=_campaign_generation_product_type(campaign),
        fallback_skill=fallback_skill,
    )
    return manifests, recommended


def _resolve_generation_skill(
    campaign: RrugcCampaignModel,
    requested_skill: str | None,
) -> tuple[str, CodexSkillManifest | None]:
    settings = get_settings()
    requested = str(requested_skill or "").strip()
    codex_home = str(
        getattr(
            settings,
            "CODEX_IMAGE_HOME",
            "/var/lib/creative-asset-manager/codex",
        )
    )
    if requested:
        return requested, load_codex_skill_manifest(codex_home, requested)

    manifests, recommended = _codex_generation_skill_catalog(campaign)
    if recommended is not None:
        return recommended.skill_name, recommended
    fallback_skill = (
        str(getattr(settings, "CODEX_IMAGE_SKILL", "worker-hat-v1")).strip()
        or "worker-hat-v1"
    )
    return fallback_skill, load_codex_skill_manifest(codex_home, fallback_skill)


def _rrugc_generation_capability(
    session: Session, tenant_id: str
) -> GenerationCapabilityResponse:
    settings = get_settings()
    selected_provider = str(
        getattr(settings, "RRUGC_IMAGE_GENERATION_PROVIDER", "gemini")
    ).strip().lower()
    if selected_provider not in {"gemini", "codex"}:
        selected_provider = "gemini"

    storage = build_managed_storage_provider(settings)
    storage_available = (
        settings.MANAGED_ASSET_STORAGE_ENABLED
        and not isinstance(storage, UnconfiguredAssetStorageProvider)
    )
    base_enabled = bool(
        settings.PROCESSING_JOBS_ENABLED
        and settings.IMAGE_GENERATION_ENABLED
        and settings.MANAGED_ASSET_STORAGE_ENABLED
    )

    reason = None
    if selected_provider == "codex":
        enabled = bool(
            base_enabled
            and getattr(settings, "CODEX_IMAGE_GENERATION_ENABLED", False)
        )
        runner = CodexImageGenRunner(
            CodexImageRunnerConfig(
                binary=str(getattr(settings, "CODEX_IMAGE_BINARY", "codex") or "codex"),
                codex_home=str(
                    getattr(
                        settings,
                        "CODEX_IMAGE_HOME",
                        "/var/lib/creative-asset-manager/codex",
                    )
                ),
                staging_root=str(settings.IMAGE_GENERATION_STAGING_ROOT),
                skill_name=str(
                    getattr(settings, "CODEX_IMAGE_SKILL", "worker-hat-v1")
                ).strip()
                or "worker-hat-v1",
                timeout_seconds=int(
                    getattr(settings, "CODEX_IMAGE_TIMEOUT_SECONDS", 900)
                ),
                model=(
                    str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip()
                    or None
                ),
            )
        )
        provider_reason = runner.capability_reason()
        available = bool(enabled and storage_available and provider_reason is None)
        if not enabled:
            reason = "Codex image generation is disabled by production settings."
        elif provider_reason is not None:
            reason = provider_reason
        elif not storage_available:
            reason = "Managed Drive is unavailable."
        return GenerationCapabilityResponse(
            enabled=enabled,
            available=available,
            provider="codex",
            model=(
                str(getattr(settings, "CODEX_IMAGE_MODEL", "")).strip()
                or "account-default"
            ),
            operation="reference_conditioned_product_edit",
            reason=reason,
        )

    image_capability = provider_capability(session, settings, tenant_id)
    gemini = next(
        (item for item in image_capability.providers if item.id == "gemini"),
        None,
    )
    enabled = bool(base_enabled and settings.GEMINI_IMAGE_GENERATION_ENABLED)
    available = bool(enabled and gemini and gemini.available and storage_available)
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
    reference_set_id = next(
        (
            str(item.get("reference_set_id"))
            for item in references
            if item.get("reference_set_id")
        ),
        None,
    )
    reference_roles: list[str] = []
    for item in references:
        role = str(item.get("role") or "").strip()
        if role and role not in reference_roles:
            reference_roles.append(role)
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
        reference_set_id=reference_set_id,
        reference_roles=reference_roles,
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



def _stage2_skill(item: Stage2SkillItem) -> Stage2SkillResponse:
    return Stage2SkillResponse(
        source=item.source,
        skill_id=item.skill_id,
        skill_name=item.skill_name,
        display_name=item.display_name,
        description=item.description,
        default_version=item.default_version,
        latest_version=item.latest_version,
        local_version=item.local_version,
        synced_version=item.synced_version,
        ready=item.ready,
        keyword_artwork_ready=item.ready and keyword_skill_compatible(item.skill_name),
        sync_state=item.sync_state,
        version_options=list(item.version_options),
    )


def _stage2_job(
    row: RrugcStage2JobModel,
    *,
    current_user_id: str | None = None,
    next_attempt_at: datetime | None = None,
) -> Stage2JobResponse:
    selected_ids = [
        str(value)
        for value in (row.selected_candidate_ids_json or [])
        if str(value).strip()
    ]
    queued_at = row.queued_at
    if queued_at is not None and queued_at.tzinfo is None:
        queued_at = queued_at.replace(tzinfo=timezone.utc)
    cancel_available_until = (
        queued_at + timedelta(seconds=STAGE2_CANCEL_GRACE_SECONDS)
        if row.status == "queued" and row.started_at is None and queued_at is not None
        else None
    )
    now = datetime.now(timezone.utc)
    can_cancel = bool(
        cancel_available_until is not None
        and cancel_available_until > now
        and current_user_id
        and row.created_by_user_id == current_user_id
    )
    return Stage2JobResponse(
        id=row.id,
        source_plan_id=row.source_plan_id,
        campaign_id=row.campaign_id,
        source_revision=row.source_revision,
        regenerated_from_job_id=row.regenerated_from_job_id,
        skill_name=row.skill_name,
        skill_source=row.skill_source or "local",
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        skill_bundle_sha256=row.skill_bundle_sha256,
        selected_candidate_ids=selected_ids,
        reference_count=len(selected_ids),
        status=row.status,
        can_cancel=can_cancel,
        cancel_available_until=cancel_available_until,
        processing_job_id=row.processing_job_id,
        next_attempt_at=(
            (next_attempt_at.replace(tzinfo=timezone.utc) if next_attempt_at.tzinfo is None else next_attempt_at)
            if row.status == "queued" and next_attempt_at is not None else None
        ),
        provider_request_id=row.provider_request_id,
        output_content_type=row.output_content_type,
        output_size_bytes=row.output_size_bytes,
        output_width=row.output_width,
        output_height=row.output_height,
        output_remote_file_id=row.output_remote_file_id,
        output_web_url=row.output_web_url,
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
            protected_queries=(
                search_query_anchors[:2]
                if row.discovery_mode == "product_context"
                else search_query_anchors
            ),
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
        discovery_mode=row.discovery_mode,
        product_context=row.product_context_json,
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


def _scout_query_key(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _scout_query_performance(
    session: Session,
    *,
    tenant_id: str,
    campaign_id: str,
    stage2_used_ids: set[str],
) -> list[ScoutQueryPerformance]:
    stats: dict[str, dict[str, object]] = {}

    def ensure(query: str) -> dict[str, object]:
        clean = " ".join(str(query or "").split())[:500]
        key = _scout_query_key(clean)
        row = stats.get(key)
        if row is None:
            row = {
                "query": clean,
                "submitted": 0,
                "created": 0,
                "existing": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "stage2_used": 0,
            }
            stats[key] = row
        return row

    recent_runs = list(session.scalars(
        select(RrugcScoutRunModel)
        .where(
            RrugcScoutRunModel.tenant_id == tenant_id,
            RrugcScoutRunModel.campaign_id == campaign_id,
        )
        .order_by(RrugcScoutRunModel.created_at.desc())
        .limit(40)
    ))
    for run in recent_runs:
        raw_stats = run.keyword_stats_json if isinstance(run.keyword_stats_json, dict) else {}
        for raw_query, values in raw_stats.items():
            if not str(raw_query or "").strip() or not isinstance(values, dict):
                continue
            row = ensure(str(raw_query))
            for field in ("submitted", "created", "existing"):
                row[field] = int(row[field]) + int(values.get(field) or 0)

    candidate_rows = list(session.scalars(
        select(RrugcCandidateModel)
        .where(
            RrugcCandidateModel.tenant_id == tenant_id,
            RrugcCandidateModel.campaign_id == campaign_id,
        )
        .order_by(RrugcCandidateModel.created_at.desc())
        .limit(2000)
    ))
    approved_statuses = set(ANALYSIS_APPROVED_STATUSES) | {
        "drive_ready",
        "importing",
        "import_queued",
    }
    for candidate in candidate_rows:
        signal = candidate.ai_signal_json if isinstance(candidate.ai_signal_json, dict) else {}
        query = str(signal.get("scout_query") or "").strip()
        if not query:
            continue
        row = ensure(query)
        if candidate.status in approved_statuses:
            row["approved"] = int(row["approved"]) + 1
        elif candidate.status == "needs_review":
            row["needs_review"] = int(row["needs_review"]) + 1
        elif str(candidate.status or "").startswith("rejected_"):
            row["rejected"] = int(row["rejected"]) + 1
        if candidate.id in stage2_used_ids:
            row["stage2_used"] = int(row["stage2_used"]) + 1

    result: list[ScoutQueryPerformance] = []
    for row in stats.values():
        submitted = int(row["submitted"])
        created = int(row["created"])
        existing = int(row["existing"])
        approved = int(row["approved"])
        rejected = int(row["rejected"])
        needs_review = int(row["needs_review"])
        stage2_used = int(row["stage2_used"])
        novelty = (created + 1.0) / (created + existing + 2.0)
        judged = approved + rejected + needs_review
        quality = (approved + 0.5 * needs_review + 1.0) / (judged + 2.0)
        used_rate = min(1.0, stage2_used / max(1.0, float(approved)))
        exploration = 1.0 / ((submitted + 1.0) ** 0.5)
        score = (
            0.30 * novelty
            + 0.40 * quality
            + 0.20 * used_rate
            + 0.10 * exploration
        )
        result.append(ScoutQueryPerformance(
            query=str(row["query"]),
            score=round(max(0.0, min(1.0, score)), 4),
            submitted=submitted,
            created=created,
            existing=existing,
            approved=approved,
            rejected=rejected,
            needs_review=needs_review,
            stage2_used=stage2_used,
        ))
    return sorted(
        result,
        key=lambda item: (item.score, item.stage2_used, item.created),
        reverse=True,
    )[:200]


def _rank_scout_queries(
    queries: list[str],
    performance: list[ScoutQueryPerformance],
) -> list[str]:
    score_by_key = {
        _scout_query_key(item.query): item.score
        for item in performance
    }
    indexed = list(enumerate(queries))
    indexed.sort(
        key=lambda pair: (
            score_by_key.get(_scout_query_key(pair[1]), 0.62),
            -pair[0],
        ),
        reverse=True,
    )
    return [query for _index, query in indexed]


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


@router.get("/reference-assets", response_model=list[ReferenceAssetResponse])
def list_reference_assets(
    source_type: str | None = Query(default=None, min_length=1, max_length=32),
    status: str | None = Query(default="ready", max_length=32),
    campaign_id: str | None = Query(default=None, max_length=36),
    profile_key: str | None = Query(default=None, min_length=1, max_length=100),
    reference_type: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return [
        _reference_asset(row)
        for row in repository.list_reference_assets(
            principal.active_tenant_id,
            source_type=source_type,
            status=status,
            campaign_id=campaign_id,
            profile_key=profile_key,
            reference_type=reference_type,
            limit=limit,
            offset=offset,
        )
    ]


@router.post(
    "/reference-assets/uploads",
    response_model=ReferenceAssetPromotionResponse,
)
async def upload_reference_asset(
    reference_type: str = Form("other"),
    campaign_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = None
    if campaign_id:
        campaign = _require_campaign(
            repository,
            principal.active_tenant_id,
            campaign_id,
        )
    content = await _read_reference_asset_upload(file)
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
        result = await RrugcReferenceLibrary(session).upload_reference(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            original_filename=file.filename,
            content=content,
            storage=storage,
            reference_type=reference_type,
            campaign=campaign,
        )
    except ReferenceLibraryError as exc:
        raise _reference_library_error(exc) from exc
    return ReferenceAssetPromotionResponse(
        asset=_reference_asset(result.asset),
        created=result.created,
    )


@router.get("/reference-assets/{reference_asset_id}", response_model=ReferenceAssetResponse)
def get_reference_asset(
    reference_asset_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = RrugcRepository(session).get_reference_asset(
        principal.active_tenant_id,
        reference_asset_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Reference asset not found")
    return _reference_asset(row)


@router.get("/reference-assets/{reference_asset_id}/image")
async def get_reference_asset_image(
    reference_asset_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = RrugcRepository(session).get_reference_asset(
        principal.active_tenant_id,
        reference_asset_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Reference asset not found")
    asset_id = row.id
    remote_file_id = row.remote_file_id
    image_format = row.image_format
    size_bytes = row.size_bytes
    content_type, _ = reference_image_content_type(image_format)
    # Managed Drive I/O can outlive a normal DB read by many seconds. Release
    # the transaction before opening/streaming the remote object so browser
    # image loads cannot pin API pool connections.
    session.close()
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
                asset_id=asset_id,
                remote_file_id=remote_file_id,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )
    except ReferenceLibraryError as exc:
        raise _reference_library_error(exc) from exc
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={
                "code": "reference_asset_unavailable",
                "message": "Reference image is unavailable.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get(
    "/reference-sets",
    response_model=list[ReferenceSetResponse],
)
def list_reference_sets(
    campaign_id: str | None = Query(default=None, max_length=36),
    include_global: bool = Query(default=False),
    status: str | None = Query(default="active", max_length=16),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    if campaign_id:
        _require_campaign(
            repository,
            principal.active_tenant_id,
            campaign_id,
        )
    return [
        _reference_set(repository, row)
        for row in repository.list_reference_sets(
            principal.active_tenant_id,
            campaign_id=campaign_id,
            include_global=include_global,
            status=status,
            limit=limit,
            offset=offset,
        )
    ]


@router.get(
    "/reference-sets/recommendation",
    response_model=ReferenceSetRecommendationResponse,
)
def recommend_reference_set(
    campaign_id: str = Query(..., min_length=1, max_length=36),
    skill_name: str | None = Query(default=None, max_length=128),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    try:
        resolved_skill_name, manifest = _resolve_generation_skill(campaign, skill_name)
    except CodexImageProviderError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if manifest is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "codex_skill_manifest_required",
                "message": "Reference recommendations require a manifest-backed Codex skill.",
            },
        )

    assets = repository.list_reference_assets(
        principal.active_tenant_id,
        status="ready",
        limit=500,
    )
    review_learning = build_reference_review_learning(
        campaign=campaign,
        attempts=repository.reviewed_generation_attempts(
            principal.active_tenant_id,
            skill_name=resolved_skill_name,
            limit=500,
        ),
    )
    reusable_reference_sets = repository.list_reference_sets(
        principal.active_tenant_id,
        campaign_id=campaign.id,
        include_global=True,
        status="active",
        limit=100,
    )
    reference_set_bindings = [
        (
            reference_set,
            repository.list_reference_set_items(
                principal.active_tenant_id,
                reference_set.id,
            ),
        )
        for reference_set in reusable_reference_sets
    ]
    reuse_recommendation = recommend_reference_set_reuse(
        campaign=campaign,
        manifest=manifest,
        reference_sets=reference_set_bindings,
        review_learning=review_learning,
    )
    discouraged_sets = discouraged_reference_sets(
        campaign=campaign,
        manifest=manifest,
        reference_sets=reference_set_bindings,
        review_learning=review_learning,
    )
    recommendations = recommend_reference_assets(
        campaign=campaign,
        manifest=manifest,
        assets=assets,
        review_learning=review_learning,
    )
    missing_required_roles = [
        item.role
        for item in recommendations
        if item.required and item.reference_asset is None
    ]
    return ReferenceSetRecommendationResponse(
        campaign_id=campaign.id,
        skill_name=resolved_skill_name,
        suggested_name=suggested_reference_set_name(campaign, manifest),
        complete=not missing_required_roles,
        missing_required_roles=missing_required_roles,
        learning_review_count=review_learning.review_count,
        learning_applied=any(
            abs(item.learning_adjustment) > 0.0001
            for item in recommendations
        ),
        reuse_recommendation=ReferenceSetReuseRecommendationResponse(
            reference_set_id=(
                reuse_recommendation.reference_set.id
                if reuse_recommendation.reference_set is not None
                else None
            ),
            reference_set_name=(
                reuse_recommendation.reference_set.name
                if reuse_recommendation.reference_set is not None
                else None
            ),
            score=reuse_recommendation.score,
            reasons=list(reuse_recommendation.reasons),
            candidate_count=reuse_recommendation.candidate_count,
            review_approved_count=reuse_recommendation.review_approved_count,
            review_rejected_count=reuse_recommendation.review_rejected_count,
        ),
        discouraged_reference_sets=[
            ReferenceSetFeedbackResponse(
                reference_set_id=item.reference_set.id,
                reference_set_name=item.reference_set.name,
                score=item.score,
                reasons=list(item.reasons),
                review_approved_count=item.review_approved_count,
                review_rejected_count=item.review_rejected_count,
            )
            for item in discouraged_sets
        ],
        items=[
            ReferenceSetRecommendationItemResponse(
                role=item.role,
                required=item.required,
                reference_asset=(
                    _reference_asset(item.reference_asset)
                    if item.reference_asset is not None
                    else None
                ),
                score=item.score,
                reasons=list(item.reasons),
                candidate_count=item.candidate_count,
                learning_adjustment=item.learning_adjustment,
                review_approved_count=item.review_approved_count,
                review_rejected_count=item.review_rejected_count,
            )
            for item in recommendations
        ],
    )


@router.post(
    "/reference-sets",
    response_model=ReferenceSetResponse,
    status_code=201,
)
def create_reference_set(
    request: ReferenceSetCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = None
    if request.campaign_id:
        campaign = _require_campaign(
            repository,
            principal.active_tenant_id,
            request.campaign_id,
        )
    try:
        row = RrugcReferenceSetService(session).create_set(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            name=request.name,
            campaign=campaign,
            profile_key=request.profile_key,
            description=request.description,
        )
    except ReferenceSetError as exc:
        raise _reference_set_error(exc) from exc
    return _reference_set(repository, row)


@router.post(
    "/reference-sets/from-skill",
    response_model=ReferenceSetResponse,
    status_code=201,
)
def create_reference_set_from_skill(
    request: ReferenceSetSkillPresetCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        request.campaign_id,
    )
    try:
        skill_name, manifest = _resolve_generation_skill(campaign, request.skill_name)
    except CodexImageProviderError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if manifest is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "codex_skill_manifest_required",
                "message": "Skill preset creation requires a manifest-backed Codex skill.",
            },
        )
    if len(request.items) > manifest.max_references:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reference_set_skill_limit_exceeded",
                "message": (
                    "Reference preset exceeds the "
                    + str(manifest.max_references)
                    + "-reference limit for $"
                    + manifest.skill_name
                    + "."
                ),
            },
        )

    allowed_roles = set(
        manifest.required_reference_roles + manifest.optional_reference_roles
    )
    normalized_roles: list[str] = []
    bindings = []
    for item in request.items:
        try:
            role = normalize_reference_role(item.role)
        except ReferenceSetError as exc:
            raise _reference_set_error(exc) from exc
        if role not in allowed_roles:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "reference_set_skill_role_not_allowed",
                    "message": (
                        "Reference role "
                        + role
                        + " is not declared by $"
                        + manifest.skill_name
                        + "."
                    ),
                },
            )
        if role in normalized_roles:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "reference_set_preset_duplicate_role",
                    "message": "Each skill preset role can only be assigned once.",
                },
            )
        reference_asset = repository.get_reference_asset(
            principal.active_tenant_id,
            item.reference_asset_id,
        )
        if reference_asset is None:
            raise HTTPException(status_code=404, detail="Reference asset not found")
        normalized_roles.append(role)
        bindings.append((reference_asset, role))

    missing_roles = manifest.missing_reference_roles(normalized_roles)
    if missing_roles:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reference_set_skill_roles_missing",
                "message": (
                    "Reference preset is missing required roles for $"
                    + manifest.skill_name
                    + ": "
                    + ", ".join(missing_roles)
                    + "."
                ),
            },
        )

    try:
        result = RrugcReferenceSetService(session).create_from_skill_preset(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            name=request.name,
            campaign=campaign,
            profile_key=request.profile_key,
            skill_name=skill_name,
            bindings=bindings,
        )
    except ReferenceSetError as exc:
        raise _reference_set_error(exc) from exc
    return _reference_set(repository, result.reference_set)


@router.get(
    "/reference-sets/{reference_set_id}",
    response_model=ReferenceSetResponse,
)
def get_reference_set(
    reference_set_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    row = repository.get_reference_set(
        principal.active_tenant_id,
        reference_set_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Reference set not found")
    return _reference_set(repository, row)


@router.post(
    "/reference-sets/{reference_set_id}/items",
    response_model=ReferenceSetItemBindingResponse,
)
def add_reference_set_item(
    reference_set_id: str,
    request: ReferenceSetItemCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    reference_set = repository.get_reference_set(
        principal.active_tenant_id,
        reference_set_id,
    )
    if reference_set is None:
        raise HTTPException(status_code=404, detail="Reference set not found")
    reference_asset = repository.get_reference_asset(
        principal.active_tenant_id,
        request.reference_asset_id,
    )
    if reference_asset is None:
        raise HTTPException(status_code=404, detail="Reference asset not found")
    try:
        result = RrugcReferenceSetService(session).add_item(
            tenant_id=principal.active_tenant_id,
            reference_set=reference_set,
            reference_asset=reference_asset,
            role=request.role,
            position=request.position,
            note=request.note,
        )
    except ReferenceSetError as exc:
        raise _reference_set_error(exc) from exc
    return ReferenceSetItemBindingResponse(
        reference_set=_reference_set(repository, result.reference_set),
        item=_reference_set_item(result.item),
        created=result.created,
    )


@router.delete(
    "/reference-sets/{reference_set_id}/items/{item_id}",
    status_code=204,
)
def remove_reference_set_item(
    reference_set_id: str,
    item_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    reference_set = repository.get_reference_set(
        principal.active_tenant_id,
        reference_set_id,
    )
    if reference_set is None:
        raise HTTPException(status_code=404, detail="Reference set not found")
    try:
        RrugcReferenceSetService(session).remove_item(
            tenant_id=principal.active_tenant_id,
            reference_set=reference_set,
            item_id=item_id,
        )
    except ReferenceSetError as exc:
        raise _reference_set_error(exc) from exc
    return Response(status_code=204)


@router.get(
    "/campaigns/{campaign_id}/reference-seeds",
    response_model=list[ReferenceSeedResponse],
)
def list_reference_seeds(
    campaign_id: str,
    profile_key: str = Query(
        "realistic-person-ugc",
        min_length=1,
        max_length=100,
    ),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    return [
        _reference_seed(row)
        for row in repository.list_reference_seeds(
            principal.active_tenant_id,
            campaign_id,
            profile_key=profile_key,
        )
    ]


@router.put(
    "/campaigns/{campaign_id}/reference-seeds/{reference_asset_id}",
    response_model=ReferenceSeedResponse,
)
def set_reference_seed(
    campaign_id: str,
    reference_asset_id: str,
    request: ReferenceSeedRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    reference_asset = repository.get_reference_asset(
        principal.active_tenant_id,
        reference_asset_id,
    )
    if reference_asset is None:
        raise HTTPException(status_code=404, detail="Reference asset not found")
    try:
        row = RrugcReferenceLibrary(session).set_seed(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            campaign=campaign,
            reference_asset=reference_asset,
            label=request.label,
            profile_key=request.profile_key,
            note=request.note,
        )
    except ReferenceLibraryError as exc:
        raise _reference_library_error(exc) from exc
    return _reference_seed(row)


@router.delete(
    "/campaigns/{campaign_id}/reference-seeds/{reference_asset_id}",
    status_code=204,
)
def clear_reference_seed(
    campaign_id: str,
    reference_asset_id: str,
    profile_key: str = Query(
        "realistic-person-ugc",
        min_length=1,
        max_length=100,
    ),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    try:
        RrugcReferenceLibrary(session).clear_seed(
            tenant_id=principal.active_tenant_id,
            campaign=campaign,
            reference_asset_id=reference_asset_id,
            profile_key=profile_key,
        )
    except ReferenceLibraryError as exc:
        raise _reference_library_error(exc) from exc
    return Response(status_code=204)


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/reference-asset",
    response_model=ReferenceAssetPromotionResponse,
)
def promote_candidate_reference_asset(
    campaign_id: str,
    candidate_id: str,
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
        principal.active_tenant_id,
        campaign_id,
        candidate_id,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        result = RrugcReferenceLibrary(session).promote_pinterest_candidate(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            campaign=campaign,
            candidate=candidate,
        )
    except ReferenceLibraryError as exc:
        raise _reference_library_error(exc) from exc
    return ReferenceAssetPromotionResponse(
        asset=_reference_asset(result.asset),
        created=result.created,
    )


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

    reference_id_value = reference.id
    remote_file_id = reference.remote_file_id
    content_type = reference.content_type
    size_bytes = reference.size_bytes
    # Detach from PostgreSQL before remote storage I/O; the response stream
    # must not own a DB connection for its lifetime.
    session.close()
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
                asset_id=reference_id_value,
                remote_file_id=remote_file_id,
                content_type=content_type,
                size_bytes=size_bytes,
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


@router.get(
    "/generation-skills",
    response_model=GenerationSkillCatalogResponse,
)
def generation_skills(
    campaign_id: str | None = Query(default=None, max_length=36),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    campaign = None
    if campaign_id:
        campaign = _require_campaign(
            repository,
            principal.active_tenant_id,
            campaign_id,
        )
    manifests, recommended = _codex_generation_skill_catalog(campaign)
    recommended_name = recommended.skill_name if recommended is not None else None
    return GenerationSkillCatalogResponse(
        recommended_skill_name=recommended_name,
        items=[
            GenerationSkillResponse(
                skill_name=manifest.skill_name,
                display_name=manifest.display_name,
                description=manifest.description,
                workflows=list(manifest.workflows),
                product_types=list(manifest.product_types),
                required_reference_roles=list(manifest.required_reference_roles),
                optional_reference_roles=list(manifest.optional_reference_roles),
                max_references=manifest.max_references,
                recommended=manifest.skill_name == recommended_name,
            )
            for manifest in manifests
        ],
    )


def _rrugc_conditional_preview(
    *,
    tenant_id: str,
    remote_file_id: str,
    size: int,
    revision: str,
    requested_version: object,
    if_none_match: object,
    default_max_age: int = 300,
) -> tuple[Response | None, str, str]:
    """A 304 is only callable after the route's tenant-scoped DB authorization."""
    etag = preview_etag(tenant_id, remote_file_id, size, revision=revision)
    control = preview_cache_control(
        requested_version, revision, default_max_age=default_max_age,
    )
    if matches_etag(if_none_match, etag):
        return Response(
            status_code=304,
            headers={"Cache-Control": control, "ETag": etag, "Vary": "Cookie"},
        ), etag, control
    return None, etag, control


_STAGE1_ORIGINAL_THUMBNAIL_SLOTS = asyncio.Semaphore(4)


async def _original_png_thumbnail_response(
    storage,
    *,
    tenant_id: str,
    asset_id: str,
    cache_namespace: str,
    cache_revision: str,
    remote_file_id: str,
    size_bytes: int | None,
    size_pixels: int,
    cache_control: str,
    etag: str | None = None,
) -> Response | None:
    """Resize the original source PNG without flattening its transparency.

    Shared by Stage 1 output and Products Stage 2 selected design previews.
    Cache per tenant, source revision and size. Single-flight and a bounded
    semaphore prevent overlapping Drive downloads from flooding the API.
    The caller releases its DB transaction before this helper runs.
    """
    if size_bytes is not None and (size_bytes <= 0 or size_bytes > MAX_PREVIEW_BYTES):
        return None
    cache_key = (
        tenant_id, cache_namespace, remote_file_id,
        f"source-alpha-v2:{cache_revision}:{size_pixels}",
    )

    async def load_original_thumbnail() -> CachedThumbnail:
        # Cap concurrent uncached source decoding to protect the VPS from a
        # burst of galleries requesting many 2048px transparent PNGs at once.
        async with _STAGE1_ORIGINAL_THUMBNAIL_SLOTS:
            stream = await storage.open_asset(OpenStoredAssetInput(
                tenant_id=tenant_id,
                asset_id=asset_id,
                remote_file_id=remote_file_id,
                content_type="image/png",
                size_bytes=size_bytes,
            ))
            try:
                content = bytearray()
                async for chunk in stream.body:
                    content.extend(chunk)
                    if len(content) > MAX_PREVIEW_BYTES:
                        raise ValueError("Stage 1 source PNG exceeds preview limit")
                thumbnail_bytes = await run_in_threadpool(
                    stage1_png_thumbnail_bytes, bytes(content), size_pixels,
                )
                return CachedThumbnail(
                    content=thumbnail_bytes, content_type="image/png", headers=(),
                )
            finally:
                await stream.close()

    try:
        cached = await thumbnail_cache.get_or_load(cache_key, load_original_thumbnail)
    except Exception:
        # Invalid/oversize PNG: do not invent alpha or serve a cropped Drive
        # preview. The endpoint falls back to streaming unchanged original.
        return None
    headers = {"Cache-Control": cache_control, "Vary": "Cookie",
               "X-Content-Type-Options": "nosniff"}
    if etag:
        headers["ETag"] = etag
    return Response(content=cached.content, media_type=cached.content_type, headers=headers)


async def _thread_geometry_preview_response(
    storage,
    *,
    tenant_id: str,
    asset_id: str,
    remote_file_id: str,
    size_bytes: int | None,
    size_pixels: int | None,
    cache_control: str,
    etag: str | None = None,
) -> Response:
    """Serve transient verified stitch geometry to CAM's cap preview widgets.

    Stage 1 gallery/Drive outputs remain opaque fabric RGB. This response is
    never a published asset and cannot be mistaken for user-provided artwork.
    """
    if size_bytes is not None and (size_bytes < 1 or size_bytes > MAX_PREVIEW_BYTES):
        raise HTTPException(status_code=413, detail="Stage 1 photo exceeds preview limits")
    cache_key = (tenant_id, "rrugc-thread-geometry-v1", remote_file_id, str(size_pixels))

    async def load_thread_geometry() -> CachedThumbnail:
        async with _STAGE1_ORIGINAL_THUMBNAIL_SLOTS:
            stream = await storage.open_asset(OpenStoredAssetInput(
                tenant_id=tenant_id, asset_id=asset_id,
                remote_file_id=remote_file_id,
                content_type="image/png", size_bytes=size_bytes,
            ))
            try:
                content = bytearray()
                async for chunk in stream.body:
                    content.extend(chunk)
                    if len(content) > MAX_PREVIEW_BYTES:
                        raise ValueError("Stage 1 photo exceeds preview limits")
                converted = await run_in_threadpool(
                    thread_preview_bytes, bytes(content), size_pixels,
                )
                return CachedThumbnail(
                    content=converted, content_type="image/png", headers=(),
                )
            finally:
                await stream.close()

    try:
        cached = await thumbnail_cache.get_or_load(cache_key, load_thread_geometry)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422,
                            detail="Verified source thread geometry is unavailable") from exc
    headers = {"Cache-Control": cache_control, "Vary": "Cookie",
               "X-Content-Type-Options": "nosniff"}
    if etag:
        headers["ETag"] = etag
    return Response(content=cached.content, media_type="image/png", headers=headers)


async def _managed_drive_thumbnail_response(
    storage,
    *,
    tenant_id: str,
    remote_file_id: str,
    size_pixels: int,
    cache_control: str,
    cache_version: str,
    etag: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> Response | None:
    """Serve a compact managed-Drive thumbnail from the shared in-memory cache."""
    if not isinstance(storage, GoogleDriveAssetStorage):
        return None

    cache_key = (
        tenant_id,
        "rrugc-source-plan",
        remote_file_id,
        f"{cache_version}:{size_pixels}:drive-thumbnail",
    )

    async def load_thumbnail() -> CachedThumbnail:
        access_token = await storage.get_access_token()
        client = None
        upstream = None
        try:
            client, upstream = await open_thumbnail_stream(
                access_token,
                remote_file_id,
                cache_key=("rrugc-managed", tenant_id, remote_file_id),
                http_client=http_client,
                size_pixels=size_pixels,
            )
            content = bytearray()
            async for chunk in upstream.aiter_raw():
                content.extend(chunk)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError("RRUGC source thumbnail response is too large")
            headers = tuple(
                (name, value)
                for name in ("last-modified",)
                if (value := upstream.headers.get(name))
            )
            return CachedThumbnail(
                content=bytes(content),
                content_type=upstream.headers.get("content-type") or "image/jpeg",
                headers=headers,
            )
        finally:
            if client is not None and upstream is not None:
                await close_thumbnail_stream(
                    client,
                    upstream,
                    close_client=client is not http_client,
                )

    try:
        cached = await thumbnail_cache.get_or_load(cache_key, load_thumbnail)
    except Exception:
        return None

    headers = dict(cached.headers)
    headers.update(
        {
            "Cache-Control": cache_control,
            "Vary": "Cookie",
        }
    )
    if etag:
        headers["ETag"] = etag
    return Response(
        content=cached.content,
        media_type=cached.content_type,
        headers=headers,
    )


SOURCE_PLAN_REFERENCE_STATUSES = frozenset(
    {"approved", "import_queued", "importing", "drive_ready"}
)
SOURCE_PLAN_PENDING_AI_STATUSES = frozenset({"analysis_queued", "analyzing"})
SOURCE_PLAN_REFERENCE_PREVIEW_STATUSES = frozenset(
    {
        *SOURCE_PLAN_REFERENCE_STATUSES,
        *SOURCE_PLAN_PENDING_AI_STATUSES,
        "analysis_failed",
        "rejected_context",
        "rejected_ai_risk",
    }
)
SOURCE_PLAN_REFERENCE_PREVIEW_LIMIT = 100


def _source_plan_reference_preview(row: RrugcCandidateModel) -> SourcePlanReferencePreviewResponse:
    signal = dict(row.ai_signal_json or {})
    source_query = str(signal.get("scout_query") or "").strip() or None
    return SourcePlanReferencePreviewResponse(
        id=row.id,
        pin_url=row.pin_url,
        image_url=row.image_url,
        status=row.status,
        picked=signal.get("reference_manual_label") == "good",
        rejected=signal.get("reference_manual_label") in {"bad", "ai"},
        manual_label=(
            signal.get("reference_manual_label")
            if signal.get("reference_manual_label") in {"good", "bad", "ai"}
            else None
        ),
        source_query=source_query,
        width=row.width,
        height=row.height,
        created_at=row.created_at,
    )


def _source_plan_group_image_response(
    row: RrugcSourcePlanModel,
) -> SourcePlanGroupImageResponse:
    return SourcePlanGroupImageResponse(
        id=row.id,
        source_name=row.source_name,
        source_relative_path=row.source_relative_path,
        source_preview_url=(
            f"/api/v1/realistic-review-ugc/source-plans/{row.id}/image"
            f"?thumbnail=true&size=128&v={row.source_revision[:16]}"
        ),
        source_web_url=row.source_web_url,
        source_width=row.source_width,
        source_height=row.source_height,
        source_size_bytes=row.source_size_bytes,
    )


def _keyword_volume_trend(
    row: RrugcKeywordVolumeModel,
) -> list[KeywordVolumeTrendPointResponse]:
    raw = row.provider_raw_json or {}

    # Prefer provider-native monthly history when the upstream endpoint exposes
    # it. The parser intentionally accepts the common Google Ads field names so
    # the UI does not need another API contract when AEBrowse starts returning
    # richer historical metrics.
    candidates = (
        raw.get("monthly_breakdown"),
        raw.get("monthly_search_volumes"),
        raw.get("monthly_searches"),
        raw.get("history"),
        raw.get("trend"),
    )
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        points: list[KeywordVolumeTrendPointResponse] = []
        for item in candidate:
            if not isinstance(item, dict):
                continue
            volume = (
                item.get("monthly_searches")
                if item.get("monthly_searches") is not None
                else item.get("search_volume")
                if item.get("search_volume") is not None
                else item.get("volume")
            )
            try:
                parsed_volume = max(0, int(float(volume)))
            except (TypeError, ValueError):
                continue
            period = str(item.get("period") or item.get("date") or "").strip()
            if not period:
                year = item.get("year")
                month = item.get("month")
                if year is not None and month is not None:
                    try:
                        month_number = int(month)
                        period = f"{int(year):04d}-{month_number:02d}"
                    except (TypeError, ValueError):
                        period = f"{year}-{month}"
            if period:
                points.append(
                    KeywordVolumeTrendPointResponse(
                        period=period,
                        volume=parsed_volume,
                    )
                )
        if points:
            return points[-24:]

    # Never synthesize a trend from point-in-time refreshes. Stage 0 should
    # only plot provider-native monthly history; otherwise the UI explicitly
    # reports that historical data is not available yet.
    return []


def _keyword_metric_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _keyword_metric_int(value: object) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _keyword_volume_response(
    row: RrugcKeywordVolumeModel, feedback: dict | None = None,
    source_agents: list[dict] | None = None,
) -> KeywordVolumeResponse:
    feedback = feedback or {}
    keys = list(feedback_targets(row, "both"))
    raw = row.provider_raw_json if isinstance(row.provider_raw_json, dict) else {}
    tm = raw.get("trademark") if isinstance(raw.get("trademark"), dict) else {}
    # Additional details must belong to this exact provider keyword screening.
    tm_is_current = trademark_evidence_is_current(row) and row.trademark_source == "aebrowse_google_ads"
    if not tm_is_current:
        tm = {}
    return KeywordVolumeResponse(
        id=row.id,
        keyword=row.keyword,
        search_volume=int(row.search_volume or 0),
        competition=row.competition,
        cpc_low=row.cpc_low,
        cpc_high=row.cpc_high,
        trademark_status=row.trademark_status if tm_is_current else "unverified",
        trademark_checked_at=row.trademark_checked_at if tm_is_current else None,
        trademark_source=row.trademark_source if tm_is_current else None,
        trademark_match_count=row.trademark_match_count if tm_is_current else None,
        trademark_class_025=tm.get("class_025") if isinstance(tm.get("class_025"), bool) else None,
        trademark_category=tm.get("category") if isinstance(tm.get("category"), str) else None,
        trademark_advice=tm.get("advice") if isinstance(tm.get("advice"), str) else None,
        trademark_details=tm.get("details") if isinstance(tm.get("details"), str) else None,
        trademark_primary_conflict=tm.get("primary_conflict") if isinstance(tm.get("primary_conflict"), dict) else None,
        trademark_matches=[entry for entry in tm.get("matches", [])[:20] if isinstance(entry, dict)] if isinstance(tm.get("matches"), list) else [],
        trademark_screened_keyword=raw.get("_trademark_screened_keyword") if tm_is_current else None,
        competition_index=_keyword_metric_int(
            (row.provider_raw_json or {}).get("competition_index")
        ),
        three_month_change_pct=_keyword_metric_float(
            (row.provider_raw_json or {}).get("three_month_change_pct")
        ),
        yoy_change_pct=_keyword_metric_float(
            (row.provider_raw_json or {}).get("yoy_change_pct")
        ),
        trend=_keyword_volume_trend(row),
        source_image_url=row.source_image_url,
        source_image_status=raw.get("_reach_product_image_status"),
        source_pin_url=row.source_pin_url,
        source_agents=source_agents or [],
        scout_keyword_feedback=feedback.get(("keyword", row.keyword_normalized), "neutral"),
        scout_pin_feedback=next((feedback.get((kind, key), "neutral") for kind, key, _ in keys if kind == "pin"), "neutral"),
        picked=bool(row.picked),
        picked_at=row.picked_at,
        favorite=bool(row.favorite),
        favorite_at=row.favorite_at,
        provider=row.provider,
        provider_account=row.provider_account,
        provider_customer_id=row.provider_customer_id,
        request_count=int(row.request_count or 0),
        fetched_at=row.fetched_at,
        last_requested_at=row.last_requested_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _source_plan_response(
    row: RrugcSourcePlanModel,
    *,
    campaign: RrugcCampaignModel | None = None,
    counts: Counter | None = None,
    excluded_counts: Counter | None = None,
    previews: list[RrugcCandidateModel] | None = None,
    embroidery_group_size: int = 1,
    source_group_images: list[RrugcSourcePlanModel] | None = None,
    source_keyword: str | None = None,
) -> SourcePlanResponse:
    campaign_counts = counts or Counter()
    campaign_excluded_counts = excluded_counts or Counter()
    usable_campaign_counts = Counter(campaign_counts)
    for status, excluded in campaign_excluded_counts.items():
        usable_campaign_counts[status] = max(
            0,
            usable_campaign_counts.get(status, 0) - int(excluded),
        )
    approved_count = sum(
        usable_campaign_counts.get(status, 0)
        for status in SOURCE_PLAN_REFERENCE_STATUSES
    )
    pending_ai_count = sum(
        usable_campaign_counts.get(status, 0)
        for status in SOURCE_PLAN_PENDING_AI_STATUSES
    )
    drive_ready_count = usable_campaign_counts.get("drive_ready", 0)
    progress_count = (
        drive_ready_count
        if campaign is not None and campaign.auto_import
        else approved_count
    )
    pipeline_count = (
        quality_pipeline_count(
            usable_campaign_counts,
            auto_import=campaign.auto_import,
        )
        if campaign is not None
        else 0
    )
    queries = (
        list(campaign.search_queries_json or [campaign.query])
        if campaign is not None
        else []
    )
    return SourcePlanResponse(
        id=row.id,
        root_folder_id=row.root_folder_id,
        source_file_id=row.source_file_id,
        source_parent_folder_id=row.source_parent_folder_id,
        source_relative_path=row.source_relative_path,
        source_name=row.source_name,
        source_mime_type=row.source_mime_type,
        source_size_bytes=row.source_size_bytes,
        source_width=row.source_width,
        source_height=row.source_height,
        source_modified_at=row.source_modified_at,
        source_web_url=row.source_web_url,
        source_preview_url=(
            f"/api/v1/realistic-review-ugc/source-plans/{row.id}/image"
            f"?thumbnail=true&size=128&v={row.source_revision[:16]}"
        ),
        source_revision=row.source_revision,
        source_keyword=source_keyword,
        analysis_revision=row.analysis_revision,
        embroidery_signature=row.embroidery_signature,
        embroidery_group_size=max(1, int(embroidery_group_size)),
        source_group_images=[
            _source_plan_group_image_response(member)
            for member in (source_group_images or [row])
        ],
        target_count=row.target_count,
        status=row.status,
        visual_context=dict(row.visual_context_json or {}) or None,
        reference_contexts=[
            str(value).strip()
            for value in (
                (
                    campaign.product_context_json.get("reference_contexts")
                    if campaign is not None
                    and isinstance(campaign.product_context_json, dict)
                    else []
                )
                or []
            )
            if str(value).strip()
        ],
        campaign_id=row.campaign_id,
        campaign_name=campaign.name if campaign is not None else None,
        campaign_status=campaign.status if campaign is not None else None,
        scout_status=campaign.scout_status if campaign is not None else None,
        auto_scout=bool(campaign.auto_scout) if campaign is not None else False,
        search_queries=[
            str(query).strip()
            for query in queries
            if str(query).strip()
        ],
        progress_count=int(progress_count),
        pipeline_count=int(pipeline_count),
        candidate_count=int(sum(campaign_counts.values())),
        approved_count=int(approved_count),
        pending_ai_count=int(pending_ai_count),
        drive_ready_count=int(drive_ready_count),
        scan_next_at=campaign.scan_next_at if campaign is not None else None,
        scan_last_completed_at=(
            campaign.scan_last_completed_at if campaign is not None else None
        ),
        reference_previews=[
            _source_plan_reference_preview(candidate)
            for candidate in (previews or [])
        ],
        last_error_code=row.last_error_code,
        analyzed_at=row.analyzed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _group_source_plan_rows(
    rows: list[RrugcSourcePlanModel],
) -> list[list[RrugcSourcePlanModel]]:
    """Canonical embroidery grouping for both Reviews Stage 3 and Stage 4."""
    from app.modules.realistic_review_ugc.embroidery_groups import group_source_plan_rows
    return group_source_plan_rows(rows)

def _rrugc_source_folder(relative_path: str) -> str:
    """Folder path relative to the Reviews source root, never a file name."""
    normalized = str(relative_path or "").replace("\\", "/").strip("/")
    return normalized.rsplit("/", 1)[0] if "/" in normalized else ""


@router.get("/source-plans/folders")
def get_source_plan_folders(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Lightweight unpaginated tree shared by Stage 3 and 4.

    Apply the same grouping used by the paginated plans endpoint so folder
    counts do not change when users paginate or sort the main table.
    """
    rows = list(session.scalars(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
        RrugcSourcePlanModel.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID,
        ~func.lower(RrugcSourcePlanModel.source_name).startswith("embroidery_", autoescape=True),
    )))
    groups = _group_source_plan_rows(rows)
    counts: dict[str, list[int]] = {}
    for members in groups:
        # One embroidery group can have color variants from multiple folders.
        # Count each group once in each folder where it has source images.
        per_folder: dict[str, int] = {}
        for member in members:
            path = _rrugc_source_folder(member.source_relative_path)
            per_folder[path] = per_folder.get(path, 0) + 1
        for folder, image_count in per_folder.items():
            value = counts.setdefault(folder, [0, 0])
            value[0] += 1
            value[1] += image_count
    return {
        "folders": [{"path": path, "group_count": counts[path][0],
                     "image_count": counts[path][1]} for path in sorted(counts)],
        "total_groups": len(groups),
    }


def _products_stage1_source_keywords(
    session: Session, tenant_id: str, plans: list[RrugcSourcePlanModel],
) -> dict[str, str]:
    """Resolve each selected Stage 1 PNG to its immutable generating keyword.

    Source-plan filenames such as design_04.png are not keywords. Join using
    the saved Stage 1 output revision, never filename/path heuristics. Bound
    IN batches and always scope both sides of the join to the active tenant.
    """
    revisions = sorted({str(plan.source_revision) for plan in plans if plan.source_revision})
    mapping: dict[str, str] = {}
    for start in range(0, len(revisions), 300):
        for revision, keyword in session.execute(
            select(RrugcImageOutputVersionModel.id, RrugcKeywordImageJobModel.keyword_text)
            .join(
                RrugcKeywordImageJobModel,
                (RrugcImageOutputVersionModel.job_id == RrugcKeywordImageJobModel.id)
                & (RrugcImageOutputVersionModel.tenant_id == RrugcKeywordImageJobModel.tenant_id),
            )
            .where(
                RrugcImageOutputVersionModel.tenant_id == tenant_id,
                RrugcImageOutputVersionModel.stage == "stage1",
                RrugcKeywordImageJobModel.tenant_id == tenant_id,
                RrugcImageOutputVersionModel.id.in_(revisions[start:start + 300]),
            )
        ):
            if str(keyword or "").strip():
                mapping[str(revision)] = str(keyword).strip()
    return mapping


@router.get("/source-plans", response_model=SourcePlanPageResponse)
def get_source_plans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    sort_by: str = Query(
        default="source",
        pattern="^(source|updated|analyzed|group_size|status|progress|refs|active_jobs|outputs)$",
    ),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    stage2_only: bool = False,
    source_prefix: str | None = Query(default=None, max_length=80),
    folder_path: str | None = Query(default=None, max_length=512),
    colorway_sources: bool = False,
    colorway_workflow: str = Query(default="reviews", pattern="^(products|reviews)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
    # Scope in SQL before pagination/stats, rather than materializing every
    # tenant source and filtering later when there are tens of thousands.
    plan_query = select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
    )
    if colorway_sources and colorway_workflow == "products":
        plan_query = plan_query.where(
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        )
    elif colorway_sources:
        plan_query = plan_query.where(
            RrugcSourcePlanModel.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID,
            func.lower(RrugcSourcePlanModel.source_name).startswith("embroidery_", autoescape=True),
        )
    else:
        # Stage 3 Pinterest discovery uses the finished source photos, not
        # embroidery-only designs. Keep those designs persisted and available
        # via colorway_sources=true for Reviews Stage 2 (13 colorways).
        # SQL filtering happens before grouping, KPI totals and pagination.
        plan_query = plan_query.where(
            RrugcSourcePlanModel.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID,
            ~func.lower(RrugcSourcePlanModel.source_name).startswith(
                "embroidery_", autoescape=True,
            ),
        )
    all_plans = list(session.scalars(plan_query.order_by(
        RrugcSourcePlanModel.source_relative_path.asc(),
        RrugcSourcePlanModel.id.asc(),
    )))

    if colorway_sources:
        from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
        # Both Stage 2 views reuse the same generator, but NEVER share input
        # namespaces. The filter runs before pagination and statistics.
        all_plans = [
            row for row in all_plans if (
                row.status != "missing"
                and row.source_mime_type.casefold() in {"image/jpeg", "image/png"}
                and row.source_name.casefold().endswith((".jpg", ".jpeg", ".png"))
                and not row.source_name.casefold().startswith("output_")
                and (
                    (colorway_workflow == "products" and
                     row.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT and
                     row.source_mime_type.casefold() == "image/png")
                    or (colorway_workflow == "reviews" and
                        row.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID and
                        row.source_name.casefold().startswith("embroidery_"))
                )
            )
        ]
    else:
        # Stage 1 selections are synthetic source plans and must not leak into
        # the Pinterest discovery/review pipeline.
        all_plans = [row for row in all_plans if row.root_folder_id == RRUGC_SOURCE_ROOT_FOLDER_ID]
    product_keywords = (
        _products_stage1_source_keywords(session, principal.active_tenant_id, all_plans)
        if colorway_sources and colorway_workflow == "products" else {}
    )
    prefix = source_prefix.strip().casefold() if isinstance(source_prefix, str) else ""
    if prefix and not colorway_sources:
        all_plans = [
            row for row in all_plans
            if row.source_name.casefold().startswith(prefix)
        ]

    needle = str(q or "").strip().lower()
    groups = [[row] for row in all_plans] if colorway_sources else _group_source_plan_rows(all_plans)
    if isinstance(folder_path, str) and not colorway_sources:
        chosen = folder_path.replace("\\", "/").strip("/")
        groups = [members for members in groups if any(
            _rrugc_source_folder(member.source_relative_path) == chosen
            or _rrugc_source_folder(member.source_relative_path).startswith(chosen + "/")
            for member in members
        )]
    if needle:
        groups = [
            members
            for members in groups
            if any(
                needle in member.source_name.lower()
                or needle in member.source_relative_path.lower()
                or needle in product_keywords.get(member.source_revision, "").casefold()
                for member in members
            )
        ]

    for members in groups:
        members.sort(
            key=lambda member: (
                member.source_relative_path.lower(),
                member.id,
            )
        )

    def representative_for(
        members: list[RrugcSourcePlanModel],
    ) -> RrugcSourcePlanModel:
        return min(
            members,
            key=lambda member: (
                0 if member.campaign_id else 1,
                0 if member.status == "ready" else 1,
                0 if member.analyzed_at is not None else 1,
                member.source_relative_path.lower(),
                member.id,
            ),
        )

    def stage2_eligible(
        members: list[RrugcSourcePlanModel],
    ) -> bool:
        representative = representative_for(members)
        visual_context = dict(representative.visual_context_json or {})
        return (
            representative.status == "ready"
            and bool(representative.campaign_id)
            and bool(
                representative.embroidery_signature
                or visual_context.get("embroidery_identity")
                or visual_context.get("embroidery_text")
            )
        )

    if stage2_only:
        groups = [members for members in groups if stage2_eligible(members)]

    # Compute sort metrics over all groups BEFORE pagination. Never sort only
    # the visible page: it makes group positions jump as users change pages.
    metric_counts = {}
    metric_auto_import: dict[str, bool] = {}
    if sort_by in {"progress", "refs"}:
        metric_campaign_ids = sorted({
            str(member.campaign_id)
            for members in groups for member in members if member.campaign_id
        })
        metric_counts = RrugcRepository(session).campaign_usable_counts_many(
            principal.active_tenant_id, metric_campaign_ids,
        ) if metric_campaign_ids else {}
        if sort_by == "progress" and metric_campaign_ids:
            metric_auto_import = {
                row.id: bool(row.auto_import)
                for row in session.scalars(select(RrugcCampaignModel).where(
                    RrugcCampaignModel.tenant_id == principal.active_tenant_id,
                    RrugcCampaignModel.id.in_(metric_campaign_ids),
                ))
            }
    metric_jobs: dict[str, Counter] = {}
    if sort_by in {"active_jobs", "outputs"}:
        all_source_ids = [member.id for members in groups for member in members]
        if all_source_ids:
            for source_id, status, count in session.execute(
                select(
                    RrugcStage2JobModel.source_plan_id,
                    RrugcStage2JobModel.status,
                    func.count(RrugcStage2JobModel.id),
                ).where(
                    RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
                    RrugcStage2JobModel.source_plan_id.in_(all_source_ids),
                    RrugcStage2JobModel.status.in_(("queued", "running", "completed")),
                ).group_by(RrugcStage2JobModel.source_plan_id, RrugcStage2JobModel.status)
            ):
                metric_jobs.setdefault(str(source_id), Counter())[str(status)] = int(count)

    def group_sort_key(
        members: list[RrugcSourcePlanModel],
    ) -> tuple[object, str, str]:
        representative = representative_for(members)
        stable_path = members[0].source_relative_path.lower()
        stable_id = members[0].id
        if sort_by == "updated":
            primary: object = max(member.updated_at.timestamp() for member in members)
        elif sort_by == "analyzed":
            analyzed_rows = [
                member.analyzed_at.timestamp()
                for member in members
                if member.analyzed_at is not None
            ]
            primary = max(analyzed_rows) if analyzed_rows else float("-inf")
        elif sort_by == "group_size":
            primary = len(members)
        elif sort_by == "status":
            primary = representative.status.lower()
        elif sort_by in {"progress", "refs"}:
            campaign = str(representative.campaign_id or "")
            counts = metric_counts.get(campaign, Counter())
            if sort_by == "refs":
                primary = counts.get("drive_ready", 0)
            else:
                primary = (
                    counts.get("drive_ready", 0)
                    if metric_auto_import.get(campaign, False)
                    else sum(counts.get(state, 0) for state in SOURCE_PLAN_REFERENCE_STATUSES)
                )
        elif sort_by in {"active_jobs", "outputs"}:
            statuses = [metric_jobs.get(member.id, Counter()) for member in members]
            primary = sum(
                counts.get(state, 0)
                for counts in statuses
                for state in (("queued", "running") if sort_by == "active_jobs" else ("completed",))
            )
        else:
            primary = stable_path
        return primary, stable_path, stable_id

    groups.sort(
        key=group_sort_key,
        reverse=sort_dir == "desc",
    )

    total = len(groups)

    # Overview numbers are computed from every filtered embroidery group before
    # pagination. Changing page must never change the dashboard KPIs.
    overview_groups = [
        (representative_for(members), members)
        for members in groups
    ]
    overview_campaign_ids = sorted({
        str(representative.campaign_id)
        for representative, _members in overview_groups
        if representative.campaign_id
    })
    overview_campaigns = {
        row.id: row
        for row in session.scalars(
            select(RrugcCampaignModel).where(
                RrugcCampaignModel.tenant_id == principal.active_tenant_id,
                RrugcCampaignModel.id.in_(overview_campaign_ids),
            )
        )
    } if overview_campaign_ids else {}
    overview_counts = RrugcRepository(session).campaign_usable_counts_many(
        principal.active_tenant_id,
        overview_campaign_ids,
    )

    overview_working_groups = 0
    overview_refs_loaded = 0
    overview_stage2_groups: list[
        tuple[RrugcSourcePlanModel, list[RrugcSourcePlanModel]]
    ] = []
    overview_ref_statuses = (
        SOURCE_PLAN_REFERENCE_PREVIEW_STATUSES - {"rejected_context", "rejected_ai_risk"}
    )
    for representative, members in overview_groups:
        campaign_id = str(representative.campaign_id or "")
        campaign = overview_campaigns.get(campaign_id)
        counts = overview_counts.get(campaign_id, Counter())
        approved_count = sum(
            counts.get(status, 0)
            for status in SOURCE_PLAN_REFERENCE_STATUSES
        )
        progress_count = (
            counts.get("drive_ready", 0)
            if campaign is not None and campaign.auto_import
            else approved_count
        )
        if (
            representative.status not in {"failed", "missing"}
            and progress_count < representative.target_count
        ):
            overview_working_groups += 1
        overview_refs_loaded += sum(
            counts.get(status, 0)
            for status in overview_ref_statuses
        )

        if stage2_eligible(members):
            overview_stage2_groups.append((representative, members))

    stage2_source_plan_ids = [
        member.id
        for _representative, members in overview_stage2_groups
        for member in members
    ]
    stage2_active_jobs = int(
        session.scalar(
            select(func.count(RrugcStage2JobModel.id)).where(
                RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
                RrugcStage2JobModel.status.in_(("queued", "running")),
                RrugcStage2JobModel.source_plan_id.in_(stage2_source_plan_ids),
            )
        ) or 0
    ) if stage2_source_plan_ids else 0
    overview = SourcePlanOverviewResponse(
        embroidery_groups=total,
        source_images=sum(len(members) for _representative, members in overview_groups),
        working_groups=overview_working_groups,
        refs_loaded=overview_refs_loaded,
        stage2_groups=len(overview_stage2_groups),
        stage2_source_images=sum(
            len(members) for _representative, members in overview_stage2_groups
        ),
        stage2_drive_ready_refs=sum(
            overview_counts.get(
                str(representative.campaign_id or ""),
                Counter(),
            ).get("drive_ready", 0)
            for representative, _members in overview_stage2_groups
        ),
        stage2_active_jobs=stage2_active_jobs,
    )

    page_groups = groups[(page - 1) * page_size : page * page_size]
    plans: list[RrugcSourcePlanModel] = []
    group_members_by_plan_id: dict[str, list[RrugcSourcePlanModel]] = {}
    for members in page_groups:
        representative = representative_for(members)
        plans.append(representative)
        group_members_by_plan_id[representative.id] = members

    campaign_ids = sorted({
        str(row.campaign_id)
        for row in plans
        if row.campaign_id
    })
    campaigns: dict[str, RrugcCampaignModel] = {}
    counts_by_campaign: dict[str, Counter] = {}
    excluded_counts_by_campaign: dict[str, Counter] = {}
    previews_by_campaign: dict[str, list[RrugcCandidateModel]] = {}
    rejected_previews_by_campaign: dict[str, list[RrugcCandidateModel]] = {}
    if campaign_ids:
        campaigns = {
            row.id: row
            for row in session.scalars(
                select(RrugcCampaignModel).where(
                    RrugcCampaignModel.tenant_id == principal.active_tenant_id,
                    RrugcCampaignModel.id.in_(campaign_ids),
                )
            )
        }
        for campaign_id, status, count in session.execute(
            select(
                RrugcCandidateModel.campaign_id,
                RrugcCandidateModel.status,
                func.count(RrugcCandidateModel.id),
            )
            .where(
                RrugcCandidateModel.tenant_id == principal.active_tenant_id,
                RrugcCandidateModel.campaign_id.in_(campaign_ids),
            )
            .group_by(
                RrugcCandidateModel.campaign_id,
                RrugcCandidateModel.status,
            )
        ):
            counts_by_campaign.setdefault(campaign_id, Counter())[status] = int(count)
        for candidate in session.scalars(
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == principal.active_tenant_id,
                RrugcCandidateModel.campaign_id.in_(campaign_ids),
                RrugcCandidateModel.status.in_(SOURCE_PLAN_REFERENCE_PREVIEW_STATUSES),
            )
            .order_by(
                RrugcCandidateModel.campaign_id.asc(),
                case(
                    (
                        RrugcCandidateModel.status.in_(
                            SOURCE_PLAN_REFERENCE_STATUSES
                        ),
                        0,
                    ),
                    (
                        RrugcCandidateModel.status.in_(
                            SOURCE_PLAN_PENDING_AI_STATUSES
                        ),
                        1,
                    ),
                    (RrugcCandidateModel.status == "analysis_failed", 2),
                    else_=3,
                ),
                RrugcCandidateModel.created_at.desc(),
            )
        ):
            signal = dict(candidate.ai_signal_json or {})
            manually_rejected = signal.get("reference_manual_label") in {"bad", "ai"}
            if manually_rejected:
                excluded_counts_by_campaign.setdefault(
                    candidate.campaign_id,
                    Counter(),
                )[candidate.status] += 1
                # Keep rejected items recoverable in the review modal, while
                # excluding them from the active thumbnail slider and counts.
                rejected_rows = rejected_previews_by_campaign.setdefault(candidate.campaign_id, [])
                if len(rejected_rows) < SOURCE_PLAN_REFERENCE_PREVIEW_LIMIT:
                    rejected_rows.append(candidate)
                continue
            if candidate.status in {"rejected_context", "rejected_ai_risk"}:
                continue
            rows = previews_by_campaign.setdefault(candidate.campaign_id, [])
            if len(rows) < SOURCE_PLAN_REFERENCE_PREVIEW_LIMIT:
                rows.append(candidate)

    items = [
        _source_plan_response(
            row,
            campaign=campaigns.get(row.campaign_id or ""),
            counts=counts_by_campaign.get(row.campaign_id or "", Counter()),
            excluded_counts=excluded_counts_by_campaign.get(
                row.campaign_id or "",
                Counter(),
            ),
            previews=(
                previews_by_campaign.get(row.campaign_id or "", [])
                + rejected_previews_by_campaign.get(row.campaign_id or "", [])
            ),
            embroidery_group_size=len(group_members_by_plan_id.get(row.id, [row])),
            source_group_images=group_members_by_plan_id.get(row.id, [row]),
            source_keyword=product_keywords.get(row.source_revision),
        )
        for row in plans
    ]
    return SourcePlanPageResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        overview=overview,
    )


def _keyword_volume_resolve_response(result) -> KeywordVolumeResolveResponse:
    return KeywordVolumeResolveResponse(
        requested=result.requested,
        provider_requested=result.provider_requested,
        cached=result.cached,
        items=[_keyword_volume_response(row) for row in result.rows],
    )


@router.get(
    "/keyword-analysis/suggestions",
    response_model=list[KeywordSuggestionResponse],
)
def suggest_keyword_analysis(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=8, ge=1, le=10),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    clean = " ".join(q.split())
    if not clean:
        return []
    # Literal matching: user-entered %, _ and backslashes are not SQL wildcards.
    escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    prefix_pattern = f"{escaped}%"
    match_pattern = f"%{escaped}%"
    prefix_match = RrugcKeywordVolumeModel.keyword.ilike(prefix_pattern, escape="\\")
    rows = session.scalars(
        select(RrugcKeywordVolumeModel)
        .where(
            RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
            RrugcKeywordVolumeModel.keyword.ilike(match_pattern, escape="\\"),
        )
        .order_by(
            case((prefix_match, 0), else_=1),
            RrugcKeywordVolumeModel.favorite.desc(),
            RrugcKeywordVolumeModel.search_volume.desc(),
            RrugcKeywordVolumeModel.keyword.asc(),
            RrugcKeywordVolumeModel.id.asc(),
        )
        .limit(limit)
    ).all()
    return [
        KeywordSuggestionResponse(
            keyword=row.keyword,
            search_volume=int(row.search_volume or 0),
            favorite=bool(row.favorite),
            picked=bool(row.picked),
        )
        for row in rows
    ]


@router.get(
    "/keyword-analysis",
    response_model=KeywordVolumePageResponse,
)
def list_keyword_analysis(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=500),
    query: str = Query(default="", max_length=200),
    usage: str = Query(default="all", pattern="^(all|unused|used)$"),
    tail: str = Query(default="all", pattern="^(all|short|mid|long)$"),
    favorites_only: bool = Query(default=False),
    suggested_only: bool = Query(default=False),
    sort_by: str = Query(
        default="search_volume",
        pattern="^(keyword|trend|platform|agent|search_volume|three_month_change|yoy_change|competition|cpc|high_cpc|trademark|created_at|fetched_at)$",
    ),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    base_conditions = [
        RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id
    ]
    # Feedback is reversible during the first 10 seconds, then omitted from
    # every Stage 0 view (search, filters, counts and pagination). Persisting
    # this in the query prevents a reload from resurrecting rejected rows.
    hidden_cutoff = datetime.now(timezone.utc) - timedelta(seconds=10)
    hidden_by_feedback = select(RrugcScoutFeedbackModel.id).where(
        RrugcScoutFeedbackModel.tenant_id == RrugcKeywordVolumeModel.tenant_id,
        RrugcScoutFeedbackModel.status == "blocked",
        RrugcScoutFeedbackModel.updated_at <= hidden_cutoff,
        or_(
            and_(
                RrugcScoutFeedbackModel.target_type == "keyword",
                RrugcScoutFeedbackModel.target_key == RrugcKeywordVolumeModel.keyword_normalized,
            ),
            and_(
                RrugcScoutFeedbackModel.target_type == "pin",
                RrugcScoutFeedbackModel.target_key == RrugcKeywordVolumeModel.source_pin_url,
            ),
        ),
    ).correlate(RrugcKeywordVolumeModel).exists()
    base_conditions.append(~hidden_by_feedback)
    clean_query = query.strip()
    if clean_query:
        base_conditions.append(
            RrugcKeywordVolumeModel.keyword.ilike(f"%{clean_query}%")
        )

    # Suggested feedback can target either the phrase or its source Pin.
    # EXISTS prevents double-counting a row suggested by both scopes.
    suggested_by_feedback = select(RrugcScoutFeedbackModel.id).where(
        RrugcScoutFeedbackModel.tenant_id == RrugcKeywordVolumeModel.tenant_id,
        RrugcScoutFeedbackModel.status == "suggested",
        or_(
            and_(
                RrugcScoutFeedbackModel.target_type == "keyword",
                RrugcScoutFeedbackModel.target_key == RrugcKeywordVolumeModel.keyword_normalized,
            ),
            and_(
                RrugcScoutFeedbackModel.target_type == "pin",
                RrugcKeywordVolumeModel.source_pin_url.is_not(None),
                RrugcScoutFeedbackModel.target_key == RrugcKeywordVolumeModel.source_pin_url,
            ),
        ),
    ).correlate(RrugcKeywordVolumeModel).exists()

    conditions = list(base_conditions)
    if suggested_only is True:
        conditions.append(suggested_by_feedback)
    if favorites_only:
        conditions.append(RrugcKeywordVolumeModel.favorite.is_(True))

    # Keyword ingestion normalizes whitespace, so one space separates each
    # word. Compute the bucket in SQL to filter before pagination/counting.
    keyword_text = func.trim(RrugcKeywordVolumeModel.keyword_normalized)
    word_count = func.length(keyword_text) - func.length(
        func.replace(keyword_text, " ", "")
    ) + 1
    if tail == "short":
        conditions.append(word_count <= 2)
    elif tail == "mid":
        conditions.extend([word_count >= 3, word_count <= 4])
    elif tail == "long":
        conditions.append(word_count >= 5)

    if usage == "used":
        conditions.append(RrugcKeywordVolumeModel.picked.is_(True))
    elif usage == "unused":
        conditions.append(RrugcKeywordVolumeModel.picked.is_(False))

    total = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(*conditions)
        ) or 0
    )
    total_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(*base_conditions)
        ) or 0
    )
    total_search_volume = int(
        session.scalar(
            select(func.coalesce(func.sum(RrugcKeywordVolumeModel.search_volume), 0))
            .where(*base_conditions)
        ) or 0
    )
    average_search_volume = (
        float(total_search_volume) / float(total_keywords)
        if total_keywords
        else 0.0
    )
    cpc_midpoint = (
        func.coalesce(
            RrugcKeywordVolumeModel.cpc_low,
            RrugcKeywordVolumeModel.cpc_high,
            0.0,
        )
        + func.coalesce(
            RrugcKeywordVolumeModel.cpc_high,
            RrugcKeywordVolumeModel.cpc_low,
            0.0,
        )
    ) / 2.0
    average_cpc_raw = session.scalar(
        select(func.avg(cpc_midpoint)).where(
            *base_conditions,
            or_(
                RrugcKeywordVolumeModel.cpc_low.is_not(None),
                RrugcKeywordVolumeModel.cpc_high.is_not(None),
            ),
        )
    )
    average_cpc = (
        round(float(average_cpc_raw), 4)
        if average_cpc_raw is not None
        else None
    )
    high_competition = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                RrugcKeywordVolumeModel.competition == "HIGH",
            )
        ) or 0
    )
    zero_volume = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                RrugcKeywordVolumeModel.search_volume <= 0,
            )
        ) or 0
    )
    short_tail_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                word_count <= 2,
            )
        ) or 0
    )
    mid_tail_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                word_count >= 3,
                word_count <= 4,
            )
        ) or 0
    )
    long_tail_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                word_count >= 5,
            )
        ) or 0
    )
    picked_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                RrugcKeywordVolumeModel.picked.is_(True),
            )
        ) or 0
    )
    favorite_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions,
                RrugcKeywordVolumeModel.favorite.is_(True),
            )
        ) or 0
    )
    suggested_keywords = int(
        session.scalar(
            select(func.count(RrugcKeywordVolumeModel.id)).where(
                *base_conditions, suggested_by_feedback,
            )
        ) or 0
    )
    competition_rank = case(
        (RrugcKeywordVolumeModel.competition == "LOW", 1),
        (RrugcKeywordVolumeModel.competition == "MEDIUM", 2),
        (RrugcKeywordVolumeModel.competition == "HIGH", 3),
        else_=None,
    )
    three_month_change = (
        RrugcKeywordVolumeModel.provider_raw_json["three_month_change_pct"].as_float()
    )
    yoy_change = (
        RrugcKeywordVolumeModel.provider_raw_json["yoy_change_pct"].as_float()
    )
    # Platform and Agent are many-to-one provenance. Correlated MIN keeps
    # exactly one keyword per row, is tenant-scoped, and uses the existing
    # (tenant_id, keyword_normalized) index without an extra pagination join.
    # For multi-source keywords, choose the first alphabetical source/agent;
    # missing attribution sorts last in both directions.
    first_origin_platform = (
        select(func.min(func.lower(RrugcKeywordAgentOriginModel.source_platform)))
        .where(
            RrugcKeywordAgentOriginModel.tenant_id == RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordAgentOriginModel.keyword_normalized == RrugcKeywordVolumeModel.keyword_normalized,
        )
        .correlate(RrugcKeywordVolumeModel)
        .scalar_subquery()
    )
    first_origin_agent = (
        select(func.min(func.lower(RrugcKeywordAgentOriginModel.agent_name)))
        .where(
            RrugcKeywordAgentOriginModel.tenant_id == RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordAgentOriginModel.keyword_normalized == RrugcKeywordVolumeModel.keyword_normalized,
        )
        .correlate(RrugcKeywordVolumeModel)
        .scalar_subquery()
    )
    platform_key = func.coalesce(
        first_origin_platform,
        case(
            (RrugcKeywordVolumeModel.source_pin_url.is_not(None), "pinterest"),
            else_=None,
        ),
    )
    # Trend uses the verified AEBrowse 3-month momentum signal for a
    # consistent ascending/descending rank. Do not treat missing or pending
    # observations as zero growth (which would distort discovery ordering).
    trend_key = case(
        (RrugcKeywordVolumeModel.provider == "pending", None),
        else_=three_month_change,
    )
    if sort_by == "keyword":
        primary_order = [
            RrugcKeywordVolumeModel.keyword.asc()
            if sort_dir == "asc"
            else RrugcKeywordVolumeModel.keyword.desc()
        ]
    elif sort_by in {"platform", "agent", "trend"}:
        sort_key = {
            "platform": platform_key,
            "agent": first_origin_agent,
            "trend": trend_key,
        }[sort_by]
        primary_order = [
            sort_key.asc().nulls_last()
            if sort_dir == "asc"
            else sort_key.desc().nulls_last()
        ]
    elif sort_by == "three_month_change":
        primary_order = [
            three_month_change.asc().nulls_last()
            if sort_dir == "asc"
            else three_month_change.desc().nulls_last()
        ]
    elif sort_by == "yoy_change":
        primary_order = [
            yoy_change.asc().nulls_last()
            if sort_dir == "asc"
            else yoy_change.desc().nulls_last()
        ]
    elif sort_by == "competition":
        primary_order = [
            competition_rank.asc().nulls_last()
            if sort_dir == "asc"
            else competition_rank.desc().nulls_last()
        ]
    elif sort_by == "cpc":
        primary_order = [
            (
                RrugcKeywordVolumeModel.cpc_low.asc().nulls_last()
                if sort_dir == "asc"
                else RrugcKeywordVolumeModel.cpc_low.desc().nulls_last()
            ),
            (
                RrugcKeywordVolumeModel.cpc_high.asc().nulls_last()
                if sort_dir == "asc"
                else RrugcKeywordVolumeModel.cpc_high.desc().nulls_last()
            ),
        ]
    elif sort_by == "high_cpc":
        primary_order = [
            RrugcKeywordVolumeModel.cpc_high.asc().nulls_last()
            if sort_dir == "asc"
            else RrugcKeywordVolumeModel.cpc_high.desc().nulls_last()
        ]
    elif sort_by == "trademark":
        # Highest risk first in descending order; unknown never means safe.
        tm_rank = case(
            (RrugcKeywordVolumeModel.trademark_status == "danger", 5),
            (RrugcKeywordVolumeModel.trademark_status == "warning", 4),
            (RrugcKeywordVolumeModel.trademark_status == "possible_match", 4),
            (RrugcKeywordVolumeModel.trademark_status == "unverified", 3),
            (RrugcKeywordVolumeModel.trademark_status == "safe", 1),
            (RrugcKeywordVolumeModel.trademark_status == "no_exact_match", 2),
            else_=3,
        )
        primary_order = [tm_rank.asc() if sort_dir == "asc" else tm_rank.desc()]
    elif sort_by == "created_at":
        primary_order = [
            RrugcKeywordVolumeModel.created_at.asc()
            if sort_dir == "asc"
            else RrugcKeywordVolumeModel.created_at.desc()
        ]
    elif sort_by == "fetched_at":
        primary_order = [
            RrugcKeywordVolumeModel.fetched_at.asc()
            if sort_dir == "asc"
            else RrugcKeywordVolumeModel.fetched_at.desc()
        ]
    else:
        primary_order = [
            RrugcKeywordVolumeModel.search_volume.asc()
            if sort_dir == "asc"
            else RrugcKeywordVolumeModel.search_volume.desc()
        ]

    rows = list(
        session.scalars(
            select(RrugcKeywordVolumeModel)
            .where(*conditions)
            .order_by(
                *primary_order,
                RrugcKeywordVolumeModel.keyword.asc(),
                RrugcKeywordVolumeModel.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    feedback = statuses_for_rows(session, principal.active_tenant_id, rows)
    from app.modules.realistic_review_ugc.keyword_agent_origins import origins_for_keywords
    origins = origins_for_keywords(session, tenant_id=principal.active_tenant_id,
                                  keywords=[row.keyword_normalized for row in rows])
    return KeywordVolumePageResponse(
        items=[_keyword_volume_response(row, feedback, origins.get(row.keyword_normalized)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        overview=KeywordVolumeOverviewResponse(
            total_keywords=total_keywords,
            total_search_volume=total_search_volume,
            average_search_volume=round(average_search_volume, 2),
            average_cpc=average_cpc,
            high_competition=high_competition,
            zero_volume=zero_volume,
            short_tail_keywords=short_tail_keywords,
            mid_tail_keywords=mid_tail_keywords,
            long_tail_keywords=long_tail_keywords,
            picked_keywords=picked_keywords,
            favorite_keywords=favorite_keywords,
            suggested_keywords=suggested_keywords,
        ),
    )


@router.patch(
    "/keyword-analysis/{keyword_id}/feedback",
    response_model=KeywordVolumeResponse,
)
def set_keyword_scout_feedback(
    keyword_id: str,
    request: ScoutFeedbackRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword row not found.")
    try:
        update_feedback(session, row, request.action, request.scope, principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _keyword_volume_response(row, statuses_for_rows(session, principal.active_tenant_id, [row]))


@router.get("/scout-metrics")
def list_scout_metrics(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant_id = principal.active_tenant_id
    metric = RrugcScoutMetricCycleModel
    last_week = datetime.now(timezone.utc) - timedelta(days=7)
    rows = session.execute(select(
        metric.agent_id, metric.machine_label, metric.mode,
        func.sum(metric.scanned_pins), func.sum(metric.found_quotes),
        func.sum(metric.new_keywords), func.sum(metric.duplicate_pins),
        func.sum(metric.errors), func.max(metric.created_at),
    ).where(metric.tenant_id == tenant_id, metric.created_at >= last_week)
     .group_by(metric.agent_id, metric.machine_label, metric.mode)).all()
    review = RrugcScoutRunModel
    review_rows = session.execute(select(
        review.agent_id,
        func.sum(review.submitted_count), func.sum(review.created_count),
        func.sum(review.existing_count), func.count(review.id),
        func.sum(case((review.status.in_(("failed", "cancelled")), 1), else_=0)),
        func.max(review.updated_at),
    ).where(review.tenant_id == tenant_id, review.started_at >= last_week)
     .group_by(review.agent_id)).all()
    keyword_base = RrugcKeywordVolumeModel.tenant_id == tenant_id
    now = datetime.now(timezone.utc)
    keyword_total = session.scalar(select(func.count(RrugcKeywordVolumeModel.id)).where(keyword_base)) or 0
    keyword_new_24h = session.scalar(select(func.count(RrugcKeywordVolumeModel.id)).where(
        keyword_base, RrugcKeywordVolumeModel.created_at >= now - timedelta(hours=24)
    )) or 0
    keyword_new_7d = session.scalar(select(func.count(RrugcKeywordVolumeModel.id)).where(
        keyword_base, RrugcKeywordVolumeModel.created_at >= last_week
    )) or 0
    fb = session.execute(select(
        RrugcScoutFeedbackModel.status, func.count(RrugcScoutFeedbackModel.id)
    ).where(RrugcScoutFeedbackModel.tenant_id == tenant_id)
     .group_by(RrugcScoutFeedbackModel.status)).all()
    pending_priority = session.scalar(select(func.count(RrugcScoutFeedbackModel.id)).where(
        RrugcScoutFeedbackModel.tenant_id == tenant_id,
        RrugcScoutFeedbackModel.status == "suggested",
        RrugcScoutFeedbackModel.processed_at.is_(None),
    )) or 0
    return {
        "items": [
            {"agent_id": r[0], "machine_label": r[1], "mode": r[2],
             "scanned_pins": int(r[3] or 0), "found_quotes": int(r[4] or 0),
             "new_keywords": int(r[5] or 0), "duplicate_pins": int(r[6] or 0),
             "errors": int(r[7] or 0), "last_activity_at": r[8]}
            for r in rows
        ],
        "review_items": [
            {
                "agent_id": r[0],
                "submitted": int(r[1] or 0),
                "new_references": int(r[2] or 0),
                "duplicates": int(r[3] or 0),
                "runs": int(r[4] or 0),
                "failed_runs": int(r[5] or 0),
                "last_activity_at": r[6],
            } for r in review_rows
        ],
        "overview": {
            "total_keywords": int(keyword_total),
            "added_24h": int(keyword_new_24h),
            "added_7d": int(keyword_new_7d),
            "priority_pending": int(pending_priority),
        },
        "feedback": {status: int(count) for status, count in fb},
        "period": "7d",
    }


@router.patch(
    "/keyword-analysis/{keyword_id}/pick",
    response_model=KeywordVolumeResponse,
)
def set_keyword_analysis_pick(
    keyword_id: str,
    request: KeywordVolumePickRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row = session.scalar(
        select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.id == keyword_id,
            RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword row not found.")

    row.picked = bool(request.picked)
    row.picked_at = datetime.now(timezone.utc) if request.picked else None
    row.picked_by_user_id = principal.user_id if request.picked else None
    session.commit()
    session.refresh(row)
    return _keyword_volume_response(
        row, statuses_for_rows(session, principal.active_tenant_id, [row]),
    )


@router.patch(
    "/keyword-analysis/{keyword_id}/favorite",
    response_model=KeywordVolumeResponse,
)
def set_keyword_analysis_favorite(
    keyword_id: str,
    request: KeywordVolumeFavoriteRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row = session.scalar(
        select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.id == keyword_id,
            RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword row not found.")

    row.favorite = bool(request.favorite)
    row.favorite_at = datetime.now(timezone.utc) if request.favorite else None
    row.favorite_by_user_id = principal.user_id if request.favorite else None
    session.commit()
    session.refresh(row)
    return _keyword_volume_response(
        row, statuses_for_rows(session, principal.active_tenant_id, [row]),
    )


@router.post(
    "/keyword-analysis/resolve",
    response_model=KeywordVolumeResolveResponse,
)
async def resolve_keyword_analysis(
    request: KeywordVolumeResolveRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        result = await RrugcKeywordVolumeService(session).resolve(
            tenant_id=principal.active_tenant_id,
            keywords=request.keywords,
            force=request.force,
            source_image_url=(
                validate_image_url(request.source_image_url)
                if request.source_image_url
                else None
            ),
            source_pin_url=(
                validate_pin_url(request.source_pin_url)
                if request.source_pin_url
                else None
            ),
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    except KeywordVolumeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return _keyword_volume_resolve_response(result)


@router.get(
    "/scout-agents/{agent_id}/keyword-analysis/summary",
    response_model=ScoutKeywordSummaryResponse,
)
def quote_scout_keyword_summary(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Tenant-scoped keyword health counters for the paired desktop Scout.

    No user/browser tokens or individual keyword strings are returned.
    """
    token = _bearer_token(authorization)
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=token
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    now = datetime.now(timezone.utc)
    base = RrugcKeywordVolumeModel.tenant_id == agent.tenant_id
    total, latest_created, latest_updated = session.execute(
        select(
            func.count(RrugcKeywordVolumeModel.id),
            func.max(RrugcKeywordVolumeModel.created_at),
            func.max(RrugcKeywordVolumeModel.updated_at),
        ).where(base)
    ).one()
    day = session.scalar(
        select(func.count(RrugcKeywordVolumeModel.id)).where(
            base, RrugcKeywordVolumeModel.created_at >= now - timedelta(hours=24)
        )
    )
    week = session.scalar(
        select(func.count(RrugcKeywordVolumeModel.id)).where(
            base, RrugcKeywordVolumeModel.created_at >= now - timedelta(days=7)
        )
    )
    pressure = scout_analysis_backpressure(session, agent.tenant_id, now=now)
    quote_gate = keyword_quote_backlog_gate(
        session, agent.tenant_id, pressure=pressure, now=now,
    )
    backup_count = len(CreativeAiCredentialRepository(
        session, None
    ).list_active_backup_providers(agent.tenant_id))
    return ScoutKeywordSummaryResponse(
        total_keywords=int(total or 0),
        added_24h=int(day or 0),
        added_7d=int(week or 0),
        analysis_pending=int(pressure["pending_jobs"]),
        analysis_oldest_wait_seconds=int(pressure["oldest_wait_seconds"]),
        analysis_backpressure_active=bool(pressure["active"]),
        keyword_fair_share_limited=bool(quote_gate["active"]),
        keyword_next_slot_seconds=int(quote_gate["retry_seconds"]),
        gemini_backup_keys_configured=backup_count,
        last_created_at=latest_created,
        last_updated_at=latest_updated,
        fetched_at=now,
    )


@router.get("/scout-agents/{agent_id}/operations-summary")
def scout_operations_summary(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Paired desktop: tenant and agent-scoped live job counters.

    This does not expose the API key or its fingerprint; the same auth token
    already used by the desktop keyword-summary endpoint is required.
    """
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=_bearer_token(authorization),
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    now = datetime.now(timezone.utc)
    result = scout_jobs_snapshot(session, agent.tenant_id, agent_id, now=now)
    repo = CreativeAiCredentialRepository(session, None)
    settings = get_settings()
    primary_metadata = repo.get_metadata(agent.tenant_id, provider="gemini")
    primary_configured = (
        primary_metadata.status == "active"
        if primary_metadata is not None
        else bool((settings.GEMINI_API_KEY or "").strip())
    )
    backups = repo.list_active_backup_providers(agent.tenant_id)
    # Capacity gate is shared with Stage 1 and Image Analysis; do not
    # incorrectly claim every configured key is available for a new request.
    maintenance = RrugcMaintenanceService(session, settings)
    available_credentials = maintenance.gemini_available_credential_count(
        agent.tenant_id, now=now,
    )
    capacity = available_credentials > 0
    analysis_pressure = scout_analysis_backpressure(session, agent.tenant_id, now=now)
    keyword_gate = keyword_quote_backlog_gate(
        session, agent.tenant_id, pressure=analysis_pressure,
        now=now, reserve=False,
    )
    result["review"]["discovery_throttled"] = (
        bool(analysis_pressure["active"])
        or review_scout_soft_throttle(
            session, agent.tenant_id, pressure=analysis_pressure, now=now,
        )
    )
    result["gemini"] = {
        "primary_configured": bool(primary_configured),
        "backup_keys": len(backups),
        "configured_keys": int(primary_configured) + len(backups),
        "unique_credentials": len(maintenance._credential_fingerprints(agent.tenant_id)),
        "daily_quota_available_credentials": available_credentials,
        "capacity_available": capacity,
        "failover_enabled": bool(backups),
        "strategy": "capacity_aware_failover",
        "review_backpressure": bool(analysis_pressure["active"]),
        "keyword_fair_share_limited": bool(keyword_gate["active"]),
        "keyword_next_slot_seconds": int(keyword_gate["retry_seconds"]),
        "keyword_max_rate_per_minute": 1 if analysis_pressure["active"] else None,
    }
    # Windows Manager observes server discovery status; its Run/Pause controls
    # remain scoped to the two local Scouts, never to this VPS service.
    from .research_discovery import discovery_status
    result["auto_discover"] = discovery_status(session, agent.tenant_id)
    return result


@router.get("/keyword-analysis/search-intelligence")
def get_keyword_search_intelligence(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    return intelligence_summary(session, principal.active_tenant_id)


@router.post("/scout-agents/{agent_id}/keyword-analysis/leases/release")
def release_stopped_scout_leases(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Only the paired agent may release its own leases on a normal stop.

    Crashes are still recovered by lease expiry; this endpoint never expires
    other agents' claims and cannot mark work complete or modify search scores.
    """
    agent = RrugcAutoScoutService(session).authenticate_agent(
        agent_id=agent_id, raw_token=_bearer_token(authorization),
    )
    from .query_intelligence import release_owned_queries
    from .scout_feedback import release_owned_priority
    return {"queries": release_owned_queries(session, agent.tenant_id, agent_id),
            "priority": release_owned_priority(session, agent.tenant_id, agent_id)}


@router.post("/scout-agents/{agent_id}/keyword-analysis/query/claim")
def claim_dynamic_keyword_query(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = RrugcAutoScoutService(session).authenticate_agent(
        agent_id=agent_id, raw_token=_bearer_token(authorization),
    )
    return {"task": claim_query(session, agent.tenant_id, agent_id)}


@router.post("/scout-agents/{agent_id}/keyword-analysis/query/renew")
def renew_dynamic_keyword_query(
    agent_id: str, request: ScoutFeedbackLeaseRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = RrugcAutoScoutService(session).authenticate_agent(
        agent_id=agent_id, raw_token=_bearer_token(authorization),
    )
    if not renew_query(session, agent.tenant_id, agent_id, request.id, request.lease_token):
        raise HTTPException(status_code=409, detail="Query lease has expired or is no longer owned.")
    return {"ok": True}


@router.post("/scout-agents/{agent_id}/keyword-analysis/query/complete")
def complete_dynamic_keyword_query(
    agent_id: str, request: ScoutQueryCompleteRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = RrugcAutoScoutService(session).authenticate_agent(
        agent_id=agent_id, raw_token=_bearer_token(authorization),
    )
    if not finish_query(
        session, agent.tenant_id, agent_id, request.id, request.lease_token,
        success=request.success, scanned_pins=request.scanned_pins,
        found_quotes=request.found_quotes, new_keywords=request.new_keywords,
        duplicate_pins=request.duplicate_pins, retryable=request.retryable,
    ):
        raise HTTPException(status_code=409, detail="Query lease is no longer owned by this Scout.")
    return {"ok": True}


@router.get("/scout-agents/{agent_id}/keyword-analysis/feedback")
def get_keyword_scout_directives(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    agent = RrugcAutoScoutService(session).authenticate_agent(agent_id=agent_id, raw_token=token)
    return blocked_targets(session, agent.tenant_id)


@router.post("/scout-agents/{agent_id}/keyword-analysis/priority/claim")
def claim_keyword_scout_priority(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    agent = RrugcAutoScoutService(session).authenticate_agent(agent_id=agent_id, raw_token=token)
    return {"task": claim_priority(session, agent.tenant_id, agent_id)}


@router.post("/scout-agents/{agent_id}/keyword-analysis/priority/renew")
def renew_keyword_scout_priority(
    agent_id: str, request: ScoutFeedbackLeaseRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    agent = RrugcAutoScoutService(session).authenticate_agent(agent_id=agent_id, raw_token=token)
    if not renew_priority(session, agent.tenant_id, agent_id, request.id, request.lease_token):
        raise HTTPException(status_code=409, detail="Priority task lease is no longer assigned to this Scout.")
    return {"ok": True}


@router.post("/scout-agents/{agent_id}/keyword-analysis/priority/complete")
def complete_keyword_scout_priority(
    agent_id: str, request: ScoutFeedbackFinishRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    agent = RrugcAutoScoutService(session).authenticate_agent(agent_id=agent_id, raw_token=token)
    if not finish_priority(session, agent.tenant_id, agent_id, request.id, request.lease_token, request.success):
        raise HTTPException(status_code=409, detail="Priority task is no longer assigned to this Scout.")
    return {"ok": True}


@router.post("/scout-agents/{agent_id}/metrics")
def record_keyword_scout_cycle(
    agent_id: str, request: ScoutMetricCycleRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    agent = RrugcAutoScoutService(session).authenticate_agent(agent_id=agent_id, raw_token=token)
    row = session.scalar(select(RrugcScoutMetricCycleModel.id).where(
        RrugcScoutMetricCycleModel.tenant_id == agent.tenant_id,
        RrugcScoutMetricCycleModel.agent_id == agent_id,
        RrugcScoutMetricCycleModel.mode == request.mode,
        RrugcScoutMetricCycleModel.cycle_id == request.cycle_id,
    ))
    if row is not None:
        return {"ok": True, "duplicate": True}
    session.add(RrugcScoutMetricCycleModel(
        tenant_id=agent.tenant_id, agent_id=agent_id, mode=request.mode,
        cycle_id=request.cycle_id, machine_label=request.machine_label,
        scanned_pins=request.scanned_pins,
        found_quotes=request.found_quotes, new_keywords=request.new_keywords,
        duplicate_pins=request.duplicate_pins, errors=request.errors,
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"ok": True, "duplicate": True}
    return {"ok": True, "duplicate": False}


@router.post(
    "/scout-agents/{agent_id}/keyword-analysis/resolve",
    response_model=KeywordVolumeResolveResponse,
)
async def quote_scout_resolve_keyword_analysis(
    agent_id: str,
    request: KeywordVolumeResolveRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id,
            raw_token=token,
        )
        result = await RrugcKeywordVolumeService(session).resolve(
            tenant_id=agent.tenant_id,
            keywords=request.keywords,
            force=request.force,
            source_agent_id=agent.id,
            source_agent_name=agent.name,
            source_channel="keyword-scout",
            source_image_url=(
                validate_image_url(request.source_image_url)
                if request.source_image_url
                else None
            ),
            source_pin_url=(
                validate_pin_url(request.source_pin_url)
                if request.source_pin_url
                else None
            ),
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    except KeywordVolumeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return _keyword_volume_resolve_response(result)


@router.post(
    "/scout-agents/{agent_id}/quote-analysis/extract",
    response_model=QuoteScoutAnalyzeResponse,
)
async def quote_scout_extract_hat_quote(
    agent_id: str,
    request: QuoteScoutAnalyzeRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    registry = None
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id,
            raw_token=token,
        )
        analysis_pressure = scout_analysis_backpressure(
            session, agent.tenant_id,
        )
        # An adaptive atomic tenant-wide quote lane protects the Review queue.
        # This reservation must commit BEFORE Gemini I/O so other Scout machines
        # can observe the same slot and the DB row lock is promptly released.
        quote_gate = keyword_quote_backlog_gate(
            session, agent.tenant_id, pressure=analysis_pressure,
            reserve=True,
        )
        if quote_gate["active"]:
            session.rollback()
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "rrugc_keyword_fair_share_wait",
                    "message": "Keyword quote analysis is waiting for its protected Gemini slot.",
                },
                headers={"Retry-After": str(quote_gate["retry_seconds"])},
            )
        session.commit()
        settings = get_settings()
        registry = build_ai_provider_registry(
            settings,
            session_factory=SessionLocal,
        )
        provider = registry.require("gemini")
        backup_providers = CreativeAiCredentialRepository(
            session, None
        ).list_active_backup_providers(agent.tenant_id)
        supplied_image_bytes: bytes | None = None
        supplied_image_mime_type: str | None = None
        if request.image_base64:
            try:
                supplied_image_bytes = base64.b64decode(
                    request.image_base64,
                    validate=True,
                )
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "quote_scout_image_base64_invalid",
                        "message": "Browser fallback image payload is invalid.",
                    },
                ) from exc
            supplied_image_mime_type = request.image_mime_type

        result = await analyze_hat_quote(
            provider=provider,
            tenant_id=agent.tenant_id,
            image_url=request.image_url,
            pin_url=request.pin_url,
            alt_text=request.alt_text,
            credential_providers=((*backup_providers, "gemini") if analysis_pressure["active"] and backup_providers else ("gemini", *backup_providers)),
            supplied_image_bytes=supplied_image_bytes,
            supplied_image_mime_type=supplied_image_mime_type,
        )
        return QuoteScoutAnalyzeResponse(
            quotes=result.quotes,
            is_target_cap=result.is_target_cap,
            confidence=result.confidence,
            provider=result.provider,
            model=result.model,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    except QuoteScoutError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except AiProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    finally:
        if registry is not None:
            await registry.aclose()


@router.get("/source-plans/{source_plan_id}/image")
async def get_source_plan_image(
    source_plan_id: str,
    request: Request,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=128, ge=128, le=1024),
    artwork: str | None = Query(default=None, max_length=64),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = session.scalar(
        select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
            RrugcSourcePlanModel.id == source_plan_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Source plan not found")
    asset_id = row.id
    remote_file_id = row.source_file_id
    content_type = row.source_mime_type
    size_bytes = row.source_size_bytes
    source_revision = row.source_revision
    # SourcePlanTable lazy-loads many Drive thumbnails concurrently. Release
    # the read transaction before remote I/O so those streams cannot exhaust
    # the API QueuePool and block auth/bootstrap requests.
    session.close()

    # Google Drive's JPEG preview can turn transparent embroidery masters
    # into a black rectangle. Opt the Product Stage 2 gallery into the same
    # original-PNG alpha-preserving resize used by Stage 1 Output.
    is_source_png = str(content_type or "").lower().split(";")[0] == "image/png"
    thread_preview = artwork == "stage1-thread-geometry-v1" and is_source_png
    alpha_preview = (
        thumbnail and artwork == "stage1-source-alpha-v2" and is_source_png
    )
    suffix = ":stage1-thread-geometry-v1" if thread_preview else (
        ":stage1-source-alpha-v2" if alpha_preview else ""
    )
    etag = f'"{source_revision}{suffix}"'
    thumbnail_cache_control = "private, max-age=31536000, immutable"
    if thumbnail and request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={
                "Cache-Control": thumbnail_cache_control,
                "ETag": etag,
                "Vary": "Cookie",
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
    if thread_preview:
        return await _thread_geometry_preview_response(
            storage, tenant_id=principal.active_tenant_id,
            asset_id=asset_id, remote_file_id=remote_file_id,
            size_bytes=size_bytes, size_pixels=size if thumbnail else None,
            cache_control=thumbnail_cache_control, etag=etag,
        )
    if thumbnail:
        if alpha_preview:
            compact = await _original_png_thumbnail_response(
                storage,
                tenant_id=principal.active_tenant_id,
                asset_id=asset_id,
                cache_namespace="rrugc-source-plan-original-png",
                cache_revision=source_revision,
                remote_file_id=remote_file_id,
                size_bytes=size_bytes,
                size_pixels=size,
                cache_control=thumbnail_cache_control,
                etag=etag,
            )
        else:
            compact = await _managed_drive_thumbnail_response(
                storage,
                tenant_id=principal.active_tenant_id,
                remote_file_id=remote_file_id,
                size_pixels=size,
                cache_control=thumbnail_cache_control,
                cache_version=source_revision,
                etag=etag,
                http_client=getattr(
                    request.app.state,
                    "google_drive_stream_client",
                    None,
                ),
            )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=asset_id,
                remote_file_id=remote_file_id,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={
                "code": "rrugc_source_image_unavailable",
                "message": "The embroidery source image is unavailable.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type or content_type,
        background=BackgroundTask(stream.close),
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": etag,
        },
    )



@router.get("/stage2-skills", response_model=Stage2SkillCatalogResponse)
def list_stage2_skills(
    refresh: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    registry_rows = ensure_skill_registry(
        session,
        tenant_id=principal.active_tenant_id,
        actor_id=principal.user_id if refresh and _can_manage_stage2_skills(principal) else None,
        refresh=refresh,
    )
    catalog = list_stage2_skill_catalog(refresh=False)
    items = enabled_catalog_items(
        session,
        tenant_id=principal.active_tenant_id,
        catalog_items=catalog.items,
    )
    version_metadata = {
        (row.source, row.skill_name): registry_payload(session, row)
        for row in registry_rows
    }
    responses = []
    for item in items:
        metadata = version_metadata.get((item.source, item.skill_name), {})
        responses.append(_stage2_skill(item).model_copy(update={
            "upload_version": metadata.get("synced_upload_version"),
            "version_labels": {
                version["version"]: version["upload_version"]
                for version in metadata.get("versions", [])
                if version.get("upload_version")
            },
        }))
    return Stage2SkillCatalogResponse(
        stage_defaults=stage_skill_defaults(session, tenant_id=principal.active_tenant_id),
        openai_configured=catalog.openai_configured,
        openai_status=catalog.openai_status,
        error_code=catalog.error_code,
        items=responses,
    )


@router.get(
    "/stage2-skills/registry",
    response_model=Stage2SkillRegistryResponse,
)
def list_stage2_skill_registry(
    refresh: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    rows = ensure_skill_registry(
        session,
        tenant_id=principal.active_tenant_id,
        actor_id=principal.user_id if refresh and _can_manage_stage2_skills(principal) else None,
        refresh=refresh,
    )
    return Stage2SkillRegistryResponse(
        stage_defaults=stage_skill_defaults(session, tenant_id=principal.active_tenant_id),
        can_manage=_can_manage_stage2_skills(principal),
        items=[
            Stage2SkillRegistryItemResponse(**registry_payload(session, row))
            for row in rows
        ],
        archived_items=[
            Stage2SkillRegistryItemResponse(**registry_payload(session, row))
            for row in session.scalars(
                select(RrugcStage2SkillRegistryModel).where(
                    RrugcStage2SkillRegistryModel.tenant_id == principal.active_tenant_id,
                    RrugcStage2SkillRegistryModel.source == "local",
                    RrugcStage2SkillRegistryModel.deleted_at.is_not(None),
                )
            ).all()
        ] if _can_manage_stage2_skills(principal) else [],
    )


def _stage1_replacement_pending(session: Session, job: RrugcKeywordImageJobModel) -> bool:
    """Do not serve prior images while a confirmed replacement is pending."""
    if job.status not in ("queued", "running") or not job.processing_job_id:
        return False
    from app.modules.processing.model import ProcessingJobModel
    processing = session.get(ProcessingJobModel, job.processing_job_id)
    return bool(processing and (processing.payload_json or {}).get("replace_outputs"))


@router.post("/keyword-images/jobs/{job_id}/versions/{version}/products-stage2")
def select_stage1_design_for_products_stage2(
    job_id: str, version: int,
    selected: bool = Query(...),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Opt Stage 1 final PNG in/out of Products Stage 2 as a durable source.

    No file copies and no changes to previous colorway outputs. This synthetic
    namespace never enters the recursive Google Drive reviews scanner.
    """
    from pathlib import PurePosixPath
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT

    job = session.scalar(select(RrugcKeywordImageJobModel).where(
        RrugcKeywordImageJobModel.tenant_id == principal.active_tenant_id,
        RrugcKeywordImageJobModel.id == job_id,
    ))
    if job is None:
        raise HTTPException(status_code=404, detail="Stage 1 job not found")
    # Selection is per persisted PNG, not per generation attempt. Retry can
    # fail or run again after the file has already been uploaded to Drive.
    # A saved design remains selectable regardless of job.status.
    version_row = session.scalar(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == principal.active_tenant_id,
        RrugcImageOutputVersionModel.stage == "stage1",
        RrugcImageOutputVersionModel.job_id == job_id,
        RrugcImageOutputVersionModel.version == version,
    ))
    if version_row is None or not version_row.remote_file_id:
        raise HTTPException(status_code=404, detail="Stage 1 design version not found")
    filename = PurePosixPath((version_row.output_name or f"design_{version:02d}.png").replace("\\", "/")).name
    if not filename.lower().endswith(".png") or (version_row.content_type or "image/png").lower() != "image/png":
        raise HTTPException(status_code=422, detail={
            "code": "products_stage1_design_requires_png",
            "message": "Products Stage 2 accepts only final PNG designs.",
        })
    plan = session.scalar(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        RrugcSourcePlanModel.source_file_id == version_row.remote_file_id,
    ).with_for_update())
    if selected and plan is None:
        plan = RrugcSourcePlanModel(
            tenant_id=principal.active_tenant_id,
            root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id=version_row.remote_file_id,
            source_parent_folder_id=None,
            source_relative_path="Stage 1/" + job_id + "/" + filename,
            source_name=filename,
            source_mime_type="image/png",
            source_size_bytes=version_row.size_bytes,
            source_width=version_row.width,
            source_height=version_row.height,
            source_revision=version_row.id,
            status="ready",
            created_by_user_id=principal.user_id,
        )
        session.add(plan)
    elif plan is not None:
        plan.status = "ready" if selected else "missing"
    if selected and plan is not None:
        # Selecting a design is the durable backend trigger for Slot 6.
        # A subsequent GET of the UI must never be required to start Scout.
        session.flush()
        ugc_scout_start(UgcScoutRequest(source_plan_id=plan.id), session=session, principal=principal)
    else:
        session.commit()
    return {"selected": bool(selected), "source_plan_id": plan.id if plan else None}


class FrontDesignSizeRequest(BaseModel):
    front_design_size: int = Field(ge=40, le=200)


@router.put("/keyword-images/jobs/{job_id}/versions/{version}/front-design-size")
def put_stage1_front_design_size(
    job_id: str, version: int, body: FrontDesignSizeRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Save the design-specific zoom, never mutate completed image/job records."""
    from app.modules.realistic_review_ugc.front_placement import (
        FrontPlacementInvalid, validate_front_design_size,
    )
    row = session.scalar(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == principal.active_tenant_id,
        RrugcImageOutputVersionModel.stage == "stage1",
        RrugcImageOutputVersionModel.job_id == job_id,
        RrugcImageOutputVersionModel.version == version,
    ).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Stage 1 design version not found")
    try:
        size = validate_front_design_size(body.front_design_size)
    except FrontPlacementInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.front_design_size = size
    session.commit()
    return {"front_design_size": size}


class FrontPlacementRequest(BaseModel):
    """Normalized 4:5 stock coordinates; optimistic revision prevents lost saves."""

    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(ge=0.10, le=0.95)
    height: float = Field(ge=0.06, le=0.65)
    upper_line: float = Field(ge=0, le=1)
    lower_line: float = Field(ge=0, le=1)
    expected_revision: int = Field(ge=0)


@router.get("/blueprint/front-placement")
def get_products_front_placement(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.front_placement import read_front_placement
    return read_front_placement(session, principal.active_tenant_id)


@router.put("/blueprint/front-placement")
def put_products_front_placement(
    body: FrontPlacementRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.front_placement import (
        FrontPlacementConflict, FrontPlacementInvalid, save_front_placement,
    )
    values = body.model_dump(exclude={"expected_revision"})
    try:
        return save_front_placement(
            session, principal.active_tenant_id, principal.user_id,
            values, body.expected_revision,
        )
    except FrontPlacementInvalid as exc:
        raise HTTPException(status_code=422, detail={
            "code": "front_placement_invalid", "message": str(exc),
        }) from exc
    except FrontPlacementConflict as exc:
        raise HTTPException(status_code=409, detail={
            "code": "front_placement_conflict", "message": str(exc),
        }) from exc
    except IntegrityError as exc:
        # Two tabs may race to insert the very first tenant calibration.
        session.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "front_placement_conflict",
            "message": "Placement changed in another tab. Reload and try again.",
        }) from exc


@router.get("/generation-jobs/{stage}/{job_id}/outputs")
def list_generation_output_versions(
    stage: str, job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    models = {
        "stage1": RrugcKeywordImageJobModel, "stage2": RrugcColorwayJobModel,
        "stage4": RrugcStage2JobModel,
    }
    model = models.get(stage)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown generation stage")
    job = session.scalar(select(model).where(
        model.tenant_id == principal.active_tenant_id, model.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    # Keep previously uploaded designs visible while a new attempt runs.
    # They are eligible for Products Stage 2 even if the retry never completes.
    rows = output_versions(session, tenant_id=principal.active_tenant_id, stage=stage, job=job)
    selected_products_sources = set()
    if stage == "stage1":
        from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
        selected_products_sources = set(session.scalars(select(RrugcSourcePlanModel.source_file_id).where(
            RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
            RrugcSourcePlanModel.status != "missing",
        )))
    front_design_sizes: dict[int, int] = {}
    if stage == "stage1":
        front_design_sizes = dict(session.execute(select(
            RrugcImageOutputVersionModel.version, RrugcImageOutputVersionModel.front_design_size,
        ).where(
            RrugcImageOutputVersionModel.tenant_id == principal.active_tenant_id,
            RrugcImageOutputVersionModel.stage == "stage1",
            RrugcImageOutputVersionModel.job_id == job_id,
        )).all())
    output_roles: dict[int, str] = {}
    if stage == "stage1" and job.skill_name == "hanh-redesign-8869-ver-4":
        # Pair by the actual processing attempt so legacy single-image outputs
        # remain distinct from validated v4 board pairs.
        by_attempt: dict[str, list[int]] = {}
        for item in rows:
            if item.get("processing_job_id"):
                by_attempt.setdefault(item["processing_job_id"], []).append(item["version"])
        for versions in by_attempt.values():
            if len(versions) == 2:
                first, second = sorted(versions)
                output_roles[first] = "design_concepts"
                output_roles[second] = "colorways"
    return {"job_id": job_id, "stage": stage, "versions": [
        {"version": row["version"], "created_at": row["created_at"],
         "width": row["width"], "height": row["height"],
         "output_role": (
             "design_concepts" if "_01_design_concepts." in (row.get("output_name") or "")
             else "colorways" if "_02_13_colorways." in (row.get("output_name") or "")
             else output_roles.get(row["version"]) if not row.get("output_name") else None
         ),
         "output_name": row.get("output_name"),
         "font_info": row.get("font_info") if stage == "stage1" else None,
         "front_design_size": front_design_sizes.get(row["version"], 100) if stage == "stage1" else None,
         "selected_for_products_stage2": stage == "stage1" and row["remote_file_id"] in selected_products_sources,
         "url": f"/api/v1/realistic-review-ugc/generation-jobs/{stage}/{job_id}/outputs/{row['version']}"}
        for row in rows]}


@router.get("/generation-jobs/{stage}/{job_id}/outputs/archive")
async def download_generation_outputs_archive(
    stage: str, job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    models = {"stage1": RrugcKeywordImageJobModel, "stage2": RrugcColorwayJobModel,
              "stage4": RrugcStage2JobModel}
    model = models.get(stage)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown generation stage")
    job = session.scalar(select(model).where(
        model.tenant_id == principal.active_tenant_id, model.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    rows = output_versions(session, tenant_id=principal.active_tenant_id, stage=stage, job=job)
    title = getattr(job, "keyword_text", None) or getattr(job, "title", None) or "outputs"
    filename = archive_filename(stage, title)
    session.close()  # Do not hold DB connections while reading Drive files.
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage unavailable")
    try:
        archive = await build_output_archive(
            storage, tenant_id=principal.active_tenant_id,
            stage=stage, job_id=job_id, rows=rows,
        )
    except OutputArchiveError as exc:
        raise HTTPException(status_code=413 if rows else 404, detail=str(exc)) from exc
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail={"code": exc.code, "message": "Unable to download every original image"}) from exc

    def chunks():
        while (chunk := archive.read(256 * 1024)):
            yield chunk
    return StreamingResponse(chunks(), media_type="application/zip",
                             background=BackgroundTask(archive.close),
                             headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                      "Cache-Control": "private, no-store",
                                      "X-Content-Type-Options": "nosniff"})


@router.get("/generation-jobs/{stage}/{job_id}/outputs/{version}")
async def get_generation_output_version(
    stage: str, job_id: str, version: int,
    thumbnail: bool = Query(default=False),
    download: bool = Query(default=False),
    size: int = Query(default=256, ge=128, le=1024),
    artwork: str | None = Query(default=None, max_length=64),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    models = {"stage1": RrugcKeywordImageJobModel, "stage2": RrugcColorwayJobModel,
              "stage4": RrugcStage2JobModel}
    model = models.get(stage)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown generation stage")
    job = session.scalar(select(model).where(
        model.tenant_id == principal.active_tenant_id, model.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    match = next((r for r in output_versions(
        session, tenant_id=principal.active_tenant_id, stage=stage, job=job
    ) if r["version"] == version), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Output version not found")
    remote_id, content_type, size_bytes = (
        match["remote_file_id"], match["content_type"], match["size_bytes"])
    # Original Stage 1 previews show their source pixels (RGBA legacy or RGB
    # fabric). Only cap-placement widgets request ephemeral thread geometry.
    stage1_png = stage == "stage1" and str(content_type or "").lower().split(";")[0] == "image/png"
    thread_preview = stage1_png and artwork == "stage1-thread-geometry-v1" and not download
    session.close()
    if thumbnail and not download:
        not_modified, etag, cache_control = _rrugc_conditional_preview(
            tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
            size=size, revision=remote_id + (":stage1-thread-geometry-v1" if thread_preview else
                                            ":stage1-source-alpha-v2" if stage1_png else ""),
            requested_version=v,
            if_none_match=if_none_match,
        )
        if not_modified is not None:
            return not_modified
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage unavailable")
    if thread_preview:
        return await _thread_geometry_preview_response(
            storage, tenant_id=principal.active_tenant_id,
            asset_id=f"rrugc-output:stage1:{job_id}:{version}",
            remote_file_id=remote_id, size_bytes=size_bytes,
            size_pixels=size if thumbnail else None,
            cache_control=cache_control if thumbnail else "private, max-age=300",
            etag=etag if thumbnail else None,
        )
    if thumbnail and not download:
        if stage1_png:
            compact = await _original_png_thumbnail_response(
                storage, tenant_id=principal.active_tenant_id,
                asset_id=f"rrugc-output:stage1:{job_id}:{version}",
                cache_namespace="rrugc-stage1-original-png",
                cache_revision=str(version), remote_file_id=remote_id,
                size_bytes=size_bytes, size_pixels=size,
                cache_control=cache_control, etag=etag,
            )
        else:
            compact = await _managed_drive_thumbnail_response(
                storage, tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
                size_pixels=size, cache_control=cache_control,
                cache_version=job_id + ":" + str(version) + ":" + remote_id,
                etag=etag,
            )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id,
            asset_id=f"rrugc-output:{stage}:{job_id}:{version}",
            remote_file_id=remote_id, content_type=content_type, size_bytes=size_bytes))
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail={"code": exc.code, "message": "Output version unavailable"}) from exc
    return StreamingResponse(stream.body, media_type=content_type or stream.content_type,
                             background=BackgroundTask(stream.close),
                             headers={"Cache-Control": "private, no-store" if download else "private, max-age=300",
                                      **({"Content-Disposition": f'attachment; filename="{output_download_name(match, stage)}"',
                                          "X-Content-Type-Options": "nosniff"} if download else {})})


@router.patch(
    "/stage2-skills/registry/{registry_id}/note",
    response_model=Stage2SkillRegistryItemResponse,
)
def update_stage2_skill_note(
    registry_id: str,
    request: Stage2SkillNoteRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = set_skill_note(session, tenant_id=principal.active_tenant_id,
                             actor_id=principal.user_id, registry_id=registry_id,
                             note=request.note)
    except Stage2SkillRegistryError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.patch("/stage2-skills/defaults/{stage}")
def update_stage_skill_default(
    stage: str,
    request: StageSkillDefaultRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        defaults = set_stage_skill_default(session, tenant_id=principal.active_tenant_id,
                                           actor_id=principal.user_id, stage=stage,
                                           registry_id=request.registry_id)
    except Stage2SkillRegistryError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"stage_defaults": defaults}


@router.get("/generation-jobs/{stage}/{job_id}/logs")
def get_generation_job_logs(
    stage: str,
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel
    models = {
        "stage1": (RrugcKeywordImageJobModel, "rrugc_keyword_image_job"),
        "stage2": (RrugcColorwayJobModel, "rrugc_colorway_job"),
        "stage4": (RrugcStage2JobModel, "rrugc_stage2_job"),
    }
    if stage not in models:
        raise HTTPException(status_code=404, detail="Unknown generation stage")
    model, entity_type = models[stage]
    job = session.scalar(select(model).where(model.id == job_id,
                                              model.tenant_id == principal.active_tenant_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    attempts = session.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == principal.active_tenant_id,
        ProcessingJobModel.entity_type == entity_type,
        ProcessingJobModel.entity_id == job_id,
    ).order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc()).limit(50)).all()
    return {
        "stage": stage, "job_id": job.id, "status": job.status,
        "skill": {"name": job.skill_name, "source": job.skill_source, "version": job.skill_version},
        "attempts": [{
            "id": attempt.id, "status": attempt.status,
            "attempt_count": attempt.attempt_count, "max_attempts": attempt.max_attempts,
            "duration_ms": attempt.processing_duration_ms,
            "error_code": attempt.last_error_code,
            "error_message": (attempt.last_error_message or "")[:500],
            "created_at": attempt.created_at, "updated_at": attempt.updated_at,
            "completed_at": attempt.completed_at,
            "execution": (
                read_codex_execution_log(
                    str(getattr(get_settings(), "IMAGE_GENERATION_STAGING_ROOT",
                                "/var/lib/creative-asset-manager/image-generation")),
                    attempt.id,
                ) if stage == "stage1" else None
            ),
        } for attempt in attempts],
    }


@router.get("/stage2-skills/registry/{registry_id}/download")
def download_installed_skill(
    registry_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Download the current local Skill ZIP for manual editing and re-upload."""
    _require_stage2_skill_admin(principal)
    row = session.scalar(select(RrugcStage2SkillRegistryModel).where(
        RrugcStage2SkillRegistryModel.id == registry_id,
        RrugcStage2SkillRegistryModel.tenant_id == principal.active_tenant_id,
        RrugcStage2SkillRegistryModel.deleted_at.is_(None),
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if row.source != "local":
        raise HTTPException(
            status_code=422,
            detail="Only locally installed Skills can be exported",
        )
    try:
        content = export_installed_stage2_skill_bundle(row.skill_name)
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{row.skill_name}.zip"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/stage2-skills/registry",
    response_model=Stage2SkillRegistryItemResponse,
)
async def create_stage2_skill(
    file: UploadFile = File(...),
    replace_existing: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    bundle = await file.read(50 * 1024 * 1024 + 1)
    try:
        row = create_skill(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            bundle_bytes=bundle,
            replace_existing=replace_existing,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.post(
    "/stage2-skills/registry/{registry_id}/upload-stage1",
    response_model=Stage2SkillRegistryItemResponse,
)
async def upload_local_skill_for_stage1(
    registry_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    bundle = await file.read(50 * 1024 * 1024 + 1)
    try:
        row = update_local_keyword_artwork_skill(
            session, tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id, registry_id=registry_id,
            bundle_bytes=bundle,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.post(
    "/stage2-skills/registry/{registry_id}/restore-stage1",
    response_model=Stage2SkillRegistryItemResponse,
)
def restore_stage1_keyword_skill(
    registry_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = restore_keyword_skill(
            session, tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id, registry_id=registry_id,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.post(
    "/stage2-skills/registry/{registry_id}/versions",
    response_model=Stage2SkillRegistryItemResponse,
)
async def create_stage2_skill_registry_version(
    registry_id: str,
    file: UploadFile = File(...),
    make_default: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    bundle = await file.read(50 * 1024 * 1024 + 1)
    try:
        row = create_skill_version(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
            bundle_bytes=bundle,
            make_default=make_default,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.patch(
    "/stage2-skills/registry/{registry_id}/enabled",
    response_model=Stage2SkillRegistryItemResponse,
)
def update_stage2_skill_enabled(
    registry_id: str,
    request: Stage2SkillEnabledRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = set_skill_enabled(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
            enabled=request.enabled,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.post(
    "/stage2-skills/registry/{registry_id}/default",
    response_model=Stage2SkillRegistryItemResponse,
)
def update_stage2_skill_default(
    registry_id: str,
    request: Stage2SkillDefaultVersionRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = set_skill_default(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
            version=request.version,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.post(
    "/stage2-skills/registry/{registry_id}/sync",
    response_model=Stage2SkillRegistryItemResponse,
)
def sync_stage2_skill_registry(
    registry_id: str,
    request: Stage2SkillSyncRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = sync_skill(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
            version=request.version,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.delete("/stage2-skills/registry/{registry_id}/versions/{version}")
def remove_stage2_skill_version(
    registry_id: str,
    version: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        row = delete_skill_version(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
            version=version,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2SkillRegistryItemResponse(**registry_payload(session, row))


@router.delete("/stage2-skills/registry/{registry_id}")
def remove_stage2_skill(
    registry_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        delete_skill(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry_id,
        )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return {"deleted": True, "registry_id": registry_id}


@router.post(
    "/stage2-skills/{skill_id}/sync",
    response_model=Stage2SkillResponse,
)
def sync_stage2_skill(
    skill_id: str,
    request: Stage2SkillSyncRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    _require_stage2_skill_admin(principal)
    try:
        registry = get_registry_row_by_skill_id(
            session,
            tenant_id=principal.active_tenant_id,
            skill_id=skill_id,
        )
        sync_skill(
            session,
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            registry_id=registry.id,
            version=request.version,
        )
        catalog = list_stage2_skill_catalog(refresh=True)
        item = next(
            (
                value for value in catalog.items
                if value.source == "openai" and value.skill_id == skill_id
            ),
            None,
        )
        if item is None:
            raise Stage2SkillRegistryError(
                "stage2_skill_not_found",
                "The synced skill could not be refreshed.",
                status_code=404,
            )
    except Stage2SkillRegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return _stage2_skill(item)


class EcommerceRunRequest(BaseModel):
    source_plan_id: str = Field(min_length=1, max_length=36)
    stage: str = Field(pattern=r"^prod_s2_ec[1-4]$")
    color_key: str = Field(default="natural-black", pattern=r"^[a-z0-9-]{3,80}$")


@router.post("/products-stage2/ecommerce/run", status_code=202)
def run_ecommerce_skill(
    body: EcommerceRunRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError, EcommerceService
    try:
        return EcommerceService(session).queue(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            source_plan_id=body.source_plan_id, stage=body.stage, color_key=body.color_key,
        )
    except EcommerceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


class SceneRunRequest(BaseModel):
    source_plan_id: str = Field(min_length=1, max_length=36)
    slot: str = Field(pattern=r"^prod_s2_(mh|ugc)$")
    color_key: str = Field(default="natural-black", pattern=r"^[a-z0-9-]{3,80}$")


@router.post("/products-stage2/ecommerce/run-scene", status_code=202)
def run_three_photo_skill(
    body: SceneRunRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError, EcommerceService
    try:
        return EcommerceService(session).queue_scene(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            source_plan_id=body.source_plan_id, slot=body.slot,
            color_key=body.color_key)
    except EcommerceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


# Slot 6 reuses the existing Review Scout campaign, ingestion and candidate QA.
# Never rewrite a campaign shared by independent embroidery source plans.
class UgcScoutRequest(BaseModel):
    source_plan_id: str = Field(min_length=1, max_length=36)


def _ugc_scout_campaign(session: Session, tenant_id: str, plan_id: str):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import _source_plan, EcommerceError
    try:
        plan = _source_plan(session, tenant_id, plan_id)
    except EcommerceError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    if not plan.campaign_id:
        raise HTTPException(409, detail={"code": "ugc_scout_campaign_missing",
            "message": "This embroidery design has no Review Scout campaign yet."})
    related = list(session.scalars(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.campaign_id == plan.campaign_id,
        RrugcSourcePlanModel.status != "missing",
    )))
    if len(related) > 1 and (not plan.embroidery_signature or any(
        other.embroidery_signature != plan.embroidery_signature
        for other in related if other.id != plan.id
    )):
        raise HTTPException(409, detail={"code": "ugc_scout_campaign_shared",
            "message": "Review Scout campaign contains other embroidery designs; cannot use its results for Slot 6."})
    campaign = session.scalar(select(RrugcCampaignModel).where(
        RrugcCampaignModel.tenant_id == tenant_id,
        RrugcCampaignModel.id == plan.campaign_id,
    ))
    if campaign is None:
        raise HTTPException(409, detail={"code": "ugc_scout_campaign_missing",
            "message": "Assigned Review Scout campaign is not available."})
    return plan, campaign


@router.get("/products-stage2/ugc-scout/{source_plan_id}")
def ugc_scout_gallery(
    source_plan_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        plan, campaign = _ugc_scout_campaign(session, principal.active_tenant_id, source_plan_id)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "ugc_scout_campaign_missing":
            return {"source_plan_id": source_plan_id, "campaign_id": None,
                    "status": "needs_scout", "target": 20, "count": 0, "items": []}
        raise
    from app.modules.realistic_review_ugc.ugc_verified_reuse import (
        load_products_ugc_references,
    )
    from app.modules.realistic_review_ugc.ugc_scene_selection import eligible_ugc_references
    # A duplicate Pinterest pin may already be human/AI-approved in another
    # campaign of this tenant. Reuse only that verified source, while keeping
    # the local per-design duplicate row and its independent feedback.
    items = load_products_ugc_references(
        session, tenant_id=principal.active_tenant_id,
        campaign_id=campaign.id,
    )
    unique = []
    for item in eligible_ugc_references(items):
        signal = item.ai_signal_json if isinstance(item.ai_signal_json, dict) else {}
        unique.append({"id": item.id, "image_url": item.image_url,
                       "pin_url": item.pin_url, "score": item.final_score,
                       "status": item.status,
                       "reused_verified": bool(getattr(item, "reused_verified_source", False)),
                       "manual_label": signal.get("reference_manual_label")
                           if signal.get("reference_manual_label") == "good" else None})
    return {"source_plan_id": plan.id, "campaign_id": campaign.id,
            "status": campaign.scout_status, "target": 20,
            "count": len(unique), "items": unique,
            "error_code": campaign.scan_last_error_code,
            "next_attempt_at": campaign.scan_next_at.isoformat() if campaign.scan_next_at else None}


@router.post("/products-stage2/ugc-scout/start", status_code=202)
def ugc_scout_start(
    body: UgcScoutRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import _source_plan, EcommerceError
    try:
        plan = _source_plan(session, principal.active_tenant_id, body.source_plan_id, lock=True)
    except EcommerceError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    if not plan.campaign_id:
        from app.modules.realistic_review_ugc.ugc_scout_bootstrap import (
            bootstrap_products_ugc_campaign,
        )
        # Idempotent, per-design bootstrap shared with background Run-all.
        bootstrap_products_ugc_campaign(session, plan=plan, user_id=principal.user_id)
    plan, campaign = _ugc_scout_campaign(session, principal.active_tenant_id, body.source_plan_id)
    if campaign.status == "completed":
        session.commit()
        return {"source_plan_id": plan.id, "status": "completed", "campaign_id": campaign.id}
    if campaign.status != "running":
        raise HTTPException(409, detail={"code": "ugc_scout_campaign_not_running",
            "message": "Review Scout campaign must be running before Slot 6 can scout."})
    if campaign.scan_lease_run_id and campaign.scan_lease_expires_at and campaign.scan_lease_expires_at > datetime.now(timezone.utc):
        return {"source_plan_id": plan.id, "status": "running", "campaign_id": campaign.id}
    # Automatic bootstrapping must never reset an existing Scout backoff,
    # claim lease or login failure simply because a page has been reopened.
    if not campaign.auto_scout:
        campaign.auto_scout = True
    if campaign.scan_next_at is None and campaign.scout_status not in {"needs_login", "error"}:
        campaign.scan_next_at = datetime.now(timezone.utc)
    session.commit()
    return {"source_plan_id": plan.id, "status": campaign.scout_status,
            "campaign_id": campaign.id}


class EcommerceRunAllSkill(BaseModel):
    source: str = Field(pattern=r"^(local|openai)$")
    skill_id: str | None = Field(default=None, max_length=255)
    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str | None = Field(default=None, max_length=64)


class EcommerceRunAllRequest(BaseModel):
    source_plan_id: str = Field(min_length=1, max_length=36)
    color_key: str = Field(default="natural-black", pattern=r"^[a-z0-9-]{3,80}$")
    colorway_skill: EcommerceRunAllSkill | None = None


@router.post("/products-stage2/ecommerce/run-all", status_code=202)
def run_all_ecommerce_skills(
    body: EcommerceRunAllRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """One durable seven-slot sequence, not parallel enqueue of 1–7."""
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError
    from app.modules.realistic_review_ugc.products_run_all import ProductsRunAllService
    try:
        return ProductsRunAllService(session).start(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            source_plan_id=body.source_plan_id, color_key=body.color_key,
            colorway_skill=body.colorway_skill.model_dump() if body.colorway_skill else None,
        )
    except EcommerceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/products-stage2/ecommerce/run-all/status")
def ecommerce_run_all_status(
    source_plan_id: str = Query(..., min_length=1, max_length=36),
    color_key: str = Query(default="natural-black", min_length=3, max_length=80),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Read-only persisted status, scoped to design, revision, tenant and cap color."""
    from app.modules.realistic_review_ugc.products_run_all import ProductsRunAllService
    return ProductsRunAllService(session).status(
        tenant_id=principal.active_tenant_id,
        source_plan_id=source_plan_id, color_key=color_key,
    )


@router.get("/products-stage2/ecommerce/jobs")
def ecommerce_jobs(
    source_plan_id: list[str] = Query(default=[]),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError, EcommerceService
    try:
        return EcommerceService(session).list(
            tenant_id=principal.active_tenant_id, source_ids=source_plan_id)
    except EcommerceError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/products-stage2/ecommerce/jobs/{job_id}/retry", status_code=202)
def retry_ecommerce_job(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError, EcommerceService
    try:
        return EcommerceService(session).retry(
            tenant_id=principal.active_tenant_id, job_id=job_id)
    except EcommerceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/products-stage2/ecommerce/jobs/{job_id}/regenerate", status_code=202)
def regenerate_ecommerce_job(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceError, EcommerceService
    try:
        return EcommerceService(session).regenerate(
            tenant_id=principal.active_tenant_id, job_id=job_id)
    except EcommerceError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/products-stage2/ecommerce/jobs/{job_id}/versions")
def ecommerce_output_versions(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel
    from app.modules.realistic_review_ugc.output_versions import output_versions
    row = session.scalar(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == principal.active_tenant_id,
        RrugcEcommerceJobModel.id == job_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Ecommerce job not found")
    return [{
        "version": item["version"], "created_at": item["created_at"],
        "output_url": f"/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/{job_id}/output?version={item['version']}",
        "width": item["width"], "height": item["height"],
    } for item in output_versions(session, tenant_id=principal.active_tenant_id,
                                  stage="ecommerce", job=row)]


@router.get("/products-stage2/ecommerce/jobs/{job_id}/logs")
def ecommerce_job_logs(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel
    from app.modules.processing.model import ProcessingJobModel
    row = session.scalar(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == principal.active_tenant_id,
        RrugcEcommerceJobModel.id == job_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Ecommerce job not found")
    attempts = list(session.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == principal.active_tenant_id,
        ProcessingJobModel.entity_type == "rrugc_ecommerce_job",
        ProcessingJobModel.entity_id == job_id,
    ).order_by(ProcessingJobModel.created_at.asc())))
    return {
        "job_id": row.id, "stage": row.stage, "status": row.status,
        "skill": row.skill_name, "error_code": row.last_error_code,
        "attempts": [{
            "id": job.id, "status": job.status, "attempt_count": job.attempt_count,
            "error_code": job.last_error_code, "error_message": job.last_error_message,
            "created_at": job.created_at, "completed_at": job.completed_at,
        } for job in attempts],
    }


@router.get("/products-stage2/ecommerce/jobs/{job_id}/output")
async def ecommerce_job_output(
    job_id: str,
    version: int | None = Query(default=None, ge=1),
    thumbnail: bool = Query(default=False),
    size: int = Query(default=256, ge=128, le=1024),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.model import (
        RrugcEcommerceJobModel, RrugcImageOutputVersionModel,
    )
    row = session.scalar(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == principal.active_tenant_id,
        RrugcEcommerceJobModel.id == job_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Ecommerce job not found")
    remote_id, mime, size_bytes = (
        row.output_remote_file_id, row.output_content_type, row.output_size_bytes)
    if version is not None:
        saved = session.scalar(select(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.tenant_id == principal.active_tenant_id,
            RrugcImageOutputVersionModel.stage == "ecommerce",
            RrugcImageOutputVersionModel.job_id == job_id,
            RrugcImageOutputVersionModel.version == version,
        ))
        if saved is None:
            raise HTTPException(status_code=404, detail="Ecommerce output version not found")
        remote_id, mime, size_bytes = saved.remote_file_id, saved.content_type, saved.size_bytes
    if not remote_id:
        raise HTTPException(status_code=409, detail="Ecommerce output not ready")
    # Keep media authorization tenant-scoped before evaluating the revision.
    # CDN/proxy deployments must not publicly cache these authenticated bytes.
    cached, etag, cache_control = _rrugc_conditional_preview(
        tenant_id=principal.active_tenant_id,
        remote_file_id=remote_id,
        size=size if thumbnail else int(size_bytes or 0),
        revision=("thumbnail:" + remote_id) if thumbnail else (str(version) if version is not None else remote_id),
        requested_version=v if thumbnail else (str(version) if version is not None else None),
        if_none_match=if_none_match,
    )
    if cached is not None:
        return cached
    session.close()
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage unavailable")
    if thumbnail:
        compact = await _managed_drive_thumbnail_response(
            storage, tenant_id=principal.active_tenant_id,
            remote_file_id=remote_id, size_pixels=size,
            cache_control=cache_control,
            cache_version="ecommerce:" + job_id + ":" + remote_id, etag=etag,
        )
        if compact is not None:
            return compact
        # Never silently serve a multi-MB original on a thumbnail request.
        raise HTTPException(status_code=503, detail={
            "code": "thumbnail_unavailable", "message": "Compact Ecommerce preview temporarily unavailable",
        })
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id,
            asset_id="rrugc-ecommerce:" + job_id,
            remote_file_id=remote_id, content_type=mime, size_bytes=size_bytes,
        ))
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail={"code": exc.code, "message": "Ecommerce output unavailable"}) from exc
    return StreamingResponse(stream.body, media_type=mime or stream.content_type,
                             background=BackgroundTask(stream.close),
                             headers={"Cache-Control": cache_control,
                                      "ETag": etag, "Vary": "Cookie"})


@router.get("/products-stage2/ecommerce/input-readiness")
def ecommerce_input_readiness(
    source_plan_id: str = Query(..., min_length=1, max_length=36),
    color_key: str = Query(default="natural-black", min_length=3, max_length=80),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Per-design server-backed inventory of durable roles; never treat UI previews as assets."""
    from app.modules.realistic_review_ugc.ecommerce_input_contract import (
        SHOTS, build_execution_preflight,
    )
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
    plan = session.scalar(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.id == source_plan_id,
        RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
    ))
    if plan is None or plan.status == "missing":
        raise HTTPException(status_code=404, detail="Product source plan not found")
    from pathlib import Path
    from app.modules.realistic_review_ugc.ecommerce_stock_evidence import release_stock_evidence
    stock = release_stock_evidence(project_root=Path(__file__).resolve().parents[5], color_key=color_key)
    import shutil
    from app.modules.realistic_review_ugc.ecommerce_pipeline import (
        EcommerceError, _front_job,
    )
    from app.modules.realistic_review_ugc.model import (
        RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel,
    )
    settings = get_settings()
    worker_ready = all((
        settings.PROCESSING_JOBS_ENABLED, settings.IMAGE_GENERATION_ENABLED,
        settings.MANAGED_ASSET_STORAGE_ENABLED, settings.CODEX_IMAGE_GENERATION_ENABLED,
        bool(shutil.which(settings.CODEX_IMAGE_BINARY or "codex")),
    ))
    assets = {"design": plan.source_file_id}
    for role, evidence in stock.items():
        if evidence.get("ready") and evidence.get("sha256"):
            assets[role] = "sha256:" + evidence["sha256"]
    try:
        front = _front_job(session, plan, color_key)
        assets["embroidered_front"] = front.output_remote_file_id
    except EcommerceError:
        pass
    slots = []
    for shot in SHOTS:
        assignment = session.get(
            RrugcStageSkillDefaultModel, (principal.active_tenant_id, shot.stage))
        skill = session.get(
            RrugcStage2SkillRegistryModel, assignment.registry_id
        ) if assignment and assignment.registry_id else None
        skill_ready = bool(
            skill and skill.tenant_id == principal.active_tenant_id
            and skill.enabled and skill.deleted_at is None
            and skill.validation_status == "valid" and skill.skill_name
        )
        slots.append(build_execution_preflight(
            shot.stage, assets, worker_ready=worker_ready, skill_ready=skill_ready,
        ))
    return {
        "source_plan_id": plan.id,
        "source_revision": plan.source_revision,
        "color_key": color_key,
        "stock_evidence": stock,
        "slots": slots,
    }


@router.get("/products-stage2/photo-pipeline/readiness")
def product_photo_pipeline_readiness(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.products_photo_pipeline import readiness
    return readiness(session=session, tenant_id=principal.active_tenant_id)


@router.get("/products/source-plans/{source_plan_id}/outputs/archive")
async def download_product_skill_outputs_archive(
    source_plan_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Download current completed outputs from seven product skill slots.

    No placeholders, source artwork, or incomplete jobs are included.
    """
    from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel
    from app.modules.realistic_review_ugc.ecommerce_input_contract import SHOTS
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
    from app.modules.realistic_review_ugc.output_downloads import MAX_ARCHIVE_FILES

    tenant_id = principal.active_tenant_id
    plan = session.scalar(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.id == source_plan_id,
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
    ))
    if plan is None or plan.status == "missing":
        raise HTTPException(status_code=404, detail="Product source plan not found")
    current_revision = plan.source_file_id
    rows = []
    for job in session.scalars(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == tenant_id,
        RrugcEcommerceJobModel.source_plan_id == source_plan_id,
        RrugcEcommerceJobModel.source_revision == plan.source_revision,
        RrugcEcommerceJobModel.status == "completed",
        RrugcEcommerceJobModel.output_remote_file_id.is_not(None),
    ).order_by(RrugcEcommerceJobModel.stage)):
        if job.stage not in {shot.stage for shot in SHOTS}:
            continue
        rows.append({
            "version": len(rows) + 1, "remote_file_id": job.output_remote_file_id,
            "content_type": job.output_content_type, "size_bytes": job.output_size_bytes,
            "created_at": job.completed_at or job.created_at,
            "output_name": job.stage + ".png",
            "asset_id": "rrugc-ecommerce:" + job.id + ":" + (job.processing_job_id or ""),
        })
    # The old Khaki/Maroon row remains accessible as history, but does not
    # belong to the 12-photo Products deliverable or its ZIP archive.
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_COLOR_KEYS
    for job in session.scalars(select(RrugcColorwayJobModel).where(
        RrugcColorwayJobModel.tenant_id == tenant_id,
        RrugcColorwayJobModel.source_plan_id == source_plan_id,
        RrugcColorwayJobModel.source_revision == plan.source_revision,
        RrugcColorwayJobModel.color_key.in_(PRODUCTS_COLOR_KEYS),
        RrugcColorwayJobModel.status == "completed",
        RrugcColorwayJobModel.output_remote_file_id.is_not(None),
    ).order_by(RrugcColorwayJobModel.color_key)):
        rows.append({
            "version": len(rows) + 1, "remote_file_id": job.output_remote_file_id,
            "content_type": job.output_content_type, "size_bytes": job.output_size_bytes,
            "created_at": job.completed_at or job.created_at,
            "output_name": "color_" + job.color_key + ".png",
            "asset_id": "rrugc-colorway:" + job.id,
        })
    for job in session.scalars(select(RrugcStage2JobModel).where(
        RrugcStage2JobModel.tenant_id == tenant_id,
        RrugcStage2JobModel.source_plan_id == source_plan_id,
        RrugcStage2JobModel.source_revision == plan.source_revision,
        RrugcStage2JobModel.status == "completed",
        RrugcStage2JobModel.output_remote_file_id.is_not(None),
    ).order_by(RrugcStage2JobModel.created_at.desc())):
        skill = job.skill_name.lower()
        slot = "multihat" if "multihat" in skill else "ugc" if "ugc" in skill else None
        if slot is None:
            continue
        rows.append({
            "version": len(rows) + 1, "remote_file_id": job.output_remote_file_id,
            "content_type": job.output_content_type, "size_bytes": job.output_size_bytes,
            "created_at": job.completed_at or job.created_at,
            "output_name": slot + "_" + job.id[:8] + ".png",
            "asset_id": "rrugc-output:stage4:" + job.id + ":1",
        })
    # One ZIP of current images, never historical versions. Keep a hard bound.
    if not rows:
        raise HTTPException(status_code=404, detail="No completed outputs available")
    if len(rows) > MAX_ARCHIVE_FILES:
        raise HTTPException(status_code=413, detail="Too many outputs")
    session.close()
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage unavailable")
    try:
        archive = await build_output_archive(
            storage, tenant_id=tenant_id, stage="products", job_id=source_plan_id,
            rows=rows,
        )
    except OutputArchiveError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail="Unable to download all original images") from exc

    def chunks():
        while (chunk := archive.read(256 * 1024)):
            yield chunk
    return StreamingResponse(
        chunks(), media_type="application/zip",
        background=BackgroundTask(archive.close),
        headers={
            "Content-Disposition": f'attachment; filename="{archive_filename("products", source_plan_id)}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/colorways/readiness")
def colorway_readiness(
    workflow: str = Query(default="reviews", pattern="^(products|reviews)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    # Authenticated and intentionally free of provider credentials and storage paths.
    return ColorwayService(session).readiness(workflow=workflow)


@router.get("/colorways")
def list_colorways(
    source_plan_id: list[str] = Query(default=[]),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        return ColorwayService(session).list(
            tenant_id=principal.active_tenant_id, source_ids=source_plan_id)
    except ColorwayError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/colorways/batch", status_code=202)
def queue_colorways(
    request: ColorwayBatchRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        return ColorwayService(session).queue(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            source_plan_ids=request.source_plan_ids,
            workflow=request.workflow,
            skill_source=request.skill_source, skill_id=request.skill_id,
            skill_name=request.skill_name, skill_version=request.skill_version,
        )
    except ColorwayError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/colorways/{job_id}/regenerate", status_code=202)
def regenerate_colorway(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = ColorwayService(session).regenerate(
            tenant_id=principal.active_tenant_id, job_id=job_id)
        return {"job_id": row.id, "status": row.status}
    except ColorwayError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/colorways/{job_id}/retry", status_code=202)
def retry_colorway(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = ColorwayService(session).retry(
            tenant_id=principal.active_tenant_id, job_id=job_id)
        return {"job_id": row.id, "status": row.status}
    except ColorwayError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/colorways/{job_id}/output")
async def colorway_output(
    job_id: str,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=256, ge=128, le=1024),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = session.scalar(select(RrugcColorwayJobModel).where(
        RrugcColorwayJobModel.id == job_id,
        RrugcColorwayJobModel.tenant_id == principal.active_tenant_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Colorway output not found")
    if not row.output_remote_file_id:
        raise HTTPException(status_code=409, detail="Colorway output not ready")
    remote_id, content_type, size_bytes = row.output_remote_file_id, row.output_content_type, row.output_size_bytes
    session.close()
    if thumbnail:
        not_modified, etag, cache_control = _rrugc_conditional_preview(
            tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
            size=size, revision=remote_id, requested_version=v,
            if_none_match=if_none_match,
        )
        if not_modified is not None:
            return not_modified
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage unavailable")
    if thumbnail:
        compact = await _managed_drive_thumbnail_response(
            storage, tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
            size_pixels=size, cache_control=cache_control,
            cache_version=job_id + ":" + remote_id, etag=etag,
        )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id,
            asset_id="rrugc-colorway:" + job_id,
            remote_file_id=remote_id, content_type=content_type, size_bytes=size_bytes,
        ))
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail={"code": exc.code, "message": "Colorway output unavailable"}) from exc
    return StreamingResponse(
        stream.body, media_type=content_type or stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


class Stage0SourceImageInput(BaseModel):
    listing_url: str = Field(min_length=20, max_length=2048)
    image_url: str = Field(min_length=20, max_length=2048)
    replace: bool = False


@router.post("/keyword-analysis/{keyword_id}/source-image")
async def set_keyword_source_image(
    keyword_id: str,
    body: Stage0SourceImageInput,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Attach an operator-supplied marketplace photo to this exact Stage 0 keyword."""
    from app.modules.realistic_review_ugc.stage0_source_images import (
        Stage0SourceImageError, attach_marketplace_image,
    )
    try:
        row = await attach_marketplace_image(
            session,
            tenant_id=principal.active_tenant_id,
            keyword_id=keyword_id,
            listing_url=body.listing_url,
            image_url=body.image_url,
            actor_id=principal.user_id,
            replace=body.replace,
        )
        return {
            "source_image_url": row.source_image_url,
            "source_image_status": "operator_supplied",
        }
    except LookupError:
        raise HTTPException(status_code=404, detail="Keyword not found")
    except Stage0SourceImageError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/keyword-analysis/detail/{keyword_id}", response_model=KeywordVolumeResponse)
def get_keyword_analysis_detail(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Fetch the exact Stage 0 record used by Stage 1 without an expensive list scan."""
    row = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return _keyword_volume_response(row, statuses_for_rows(session, principal.active_tenant_id, [row]))


@router.post("/keyword-images/manual", status_code=202)
def create_manual_keyword_image(
    request: KeywordImageManualCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        job, created = KeywordImageService(session).queue_manual(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            text=request.keyword,
            skill_source=request.skill_source,
            skill_id=request.skill_id,
            skill_name=request.skill_name,
            skill_version=request.skill_version,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {
        "created": created, "job_id": job.id,
        "keyword_id": job.keyword_id, "status": job.status,
    }


@router.get("/keyword-images", response_model=KeywordImagePageResponse)
def list_keyword_images(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=150),
    status: str = Query(default="all", pattern="^(all|not_run|queued|running|completed|failed)$"),
    sort_by: str = Query(default="finished_at", pattern="^(keyword|search_volume|picked_at|duration|finished_at)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    return KeywordImageService(session).list_used(
        tenant_id=principal.active_tenant_id,
        page=page, page_size=page_size, q=q, status=status,
        sort_by=sort_by, sort_dir=sort_dir,
    )



@router.post("/keyword-images/batch", status_code=202)
def queue_keyword_images_batch(
    request: KeywordImageCreateRequest,
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Queue used keywords without a job; idempotently skip finished/active work."""
    tenant_id = principal.active_tenant_id
    keyword_ids = list(session.scalars(
        select(RrugcKeywordVolumeModel.id).outerjoin(
            RrugcKeywordImageJobModel,
            (RrugcKeywordImageJobModel.keyword_id == RrugcKeywordVolumeModel.id)
            & (RrugcKeywordImageJobModel.tenant_id == tenant_id),
        ).where(
            RrugcKeywordVolumeModel.tenant_id == tenant_id,
            RrugcKeywordVolumeModel.picked.is_(True),
            RrugcKeywordImageJobModel.id.is_(None),
        ).order_by(RrugcKeywordVolumeModel.search_volume.desc(), RrugcKeywordVolumeModel.id)
        .limit(limit),
    ))
    queued = 0
    for keyword_id in keyword_ids:
        try:
            _, created = KeywordImageService(session).queue(
                tenant_id=tenant_id, user_id=principal.user_id, keyword_id=keyword_id,
                skill_source=request.skill_source, skill_id=request.skill_id,
                skill_name=request.skill_name, skill_version=request.skill_version,
                prompt=request.prompt,
            )
        except KeywordImageError as exc:
            raise HTTPException(status_code=exc.status_code,
                                detail={"code": exc.code, "message": exc.message}) from exc
        if created:
            queued += 1
    remaining = int(session.scalar(select(func.count(RrugcKeywordVolumeModel.id)).outerjoin(
        RrugcKeywordImageJobModel,
        (RrugcKeywordImageJobModel.keyword_id == RrugcKeywordVolumeModel.id)
        & (RrugcKeywordImageJobModel.tenant_id == tenant_id),
    ).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
        RrugcKeywordImageJobModel.id.is_(None),
    )) or 0)
    return {"queued": queued, "remaining": remaining}


@router.post("/keyword-images/{keyword_id}", status_code=202)
def create_keyword_image(
    keyword_id: str,
    request: KeywordImageCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, created = KeywordImageService(session).queue(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            keyword_id=keyword_id,
            skill_source=request.skill_source,
            skill_id=request.skill_id,
            skill_name=request.skill_name,
            skill_version=request.skill_version,
            prompt=request.prompt,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"created": created, "job_id": row.id, "keyword_id": row.keyword_id, "status": row.status}


@router.post("/keyword-images/{keyword_id}/regenerate", status_code=202)
def regenerate_keyword_image(
    keyword_id: str,
    request: KeywordImageCreateRequest | None = None,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    if not request or not request.confirm_replace_outputs:
        raise HTTPException(status_code=422, detail="Confirmation required to replace Stage 1 outputs")
    try:
        row = KeywordImageService(session).regenerate(
            tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
            skill_source=request.skill_source if request else None,
            skill_id=request.skill_id if request else None,
            skill_name=request.skill_name if request else None,
            skill_version=request.skill_version if request else None,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"job_id": row.id, "keyword_id": row.keyword_id, "status": row.status}


@router.post("/keyword-images/{keyword_id}/retry", status_code=202)
def retry_keyword_image(
    keyword_id: str,
    request: KeywordImageCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    if not request.confirm_replace_outputs:
        raise HTTPException(status_code=422, detail="Confirmation required to replace Stage 1 outputs")
    try:
        row = KeywordImageService(session).retry(
            tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"job_id": row.id, "keyword_id": row.keyword_id, "status": row.status}


@router.post("/keyword-images/{keyword_id}/resume-upload", status_code=202)
def resume_keyword_image_upload(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = KeywordImageService(session).resume_upload(
            tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"job_id": row.id, "keyword_id": row.keyword_id, "status": row.status}


@router.post("/keyword-images/{keyword_id}/publish-available", status_code=202)
def publish_available_keyword_images(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Explicitly publish real edited PNG checkpoints; never generate replacements."""
    try:
        row = KeywordImageService(session).publish_available(
            tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
        )
    except KeywordImageError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"job_id": row.id, "keyword_id": row.keyword_id, "status": row.status}


@router.get("/keyword-images/jobs/{job_id}/status", response_model=KeywordImageRowResponse)
def get_keyword_image_job_status(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.processing.model import ProcessingJobModel
    row = session.scalar(select(RrugcKeywordImageJobModel).where(
        RrugcKeywordImageJobModel.id == job_id,
        RrugcKeywordImageJobModel.tenant_id == principal.active_tenant_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword generation not found")
    processing = session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
    replacing = _stage1_replacement_pending(session, row)
    return {
        "keyword_id": row.keyword_id, "keyword": row.keyword_text,
        "search_volume": 0, "status": effective_status(row, processing),
        "job_id": row.id, "skill_name": row.skill_name,
        "skill_version": row.skill_version, "retry_count": row.retry_count,
        "saved_output_count": int(session.scalar(select(func.count()).select_from(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.tenant_id == principal.active_tenant_id,
            RrugcImageOutputVersionModel.stage == "stage1",
            RrugcImageOutputVersionModel.job_id == row.id,
        )) or 0),
        "attempt_count": processing.attempt_count if processing else 0,
        "max_attempts": processing.max_attempts if processing else 1,
        "error_code": row.last_error_code or (processing.last_error_code if processing else None),
        "error_message": row.last_error_message or (processing.last_error_message if processing else None),
        "output_url": "/api/v1/realistic-review-ugc/keyword-images/jobs/" + row.id + "/output" if row.output_remote_file_id and not replacing else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "started_at": (row.started_at or (processing.claimed_at if processing else None)).isoformat()
            if (row.started_at or (processing.claimed_at if processing else None)) else None,
        "finished_at": (row.completed_at or (processing.completed_at if processing and effective_status(row, processing) in ("completed", "failed") else None)).isoformat()
            if (row.completed_at or (processing.completed_at if processing and effective_status(row, processing) in ("completed", "failed") else None)) else None,
    }


@router.get("/keyword-images/jobs/{job_id}/output")
async def keyword_image_output(
    job_id: str,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=256, ge=128, le=1024),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = session.scalar(select(RrugcKeywordImageJobModel).where(
        RrugcKeywordImageJobModel.id == job_id,
        RrugcKeywordImageJobModel.tenant_id == principal.active_tenant_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Keyword generation not found")
    if _stage1_replacement_pending(session, row) or not row.output_remote_file_id:
        raise HTTPException(status_code=409, detail="Keyword generation is not ready")
    remote_id, content_type, size_bytes = row.output_remote_file_id, row.output_content_type, row.output_size_bytes
    session.close()
    if thumbnail:
        not_modified, etag, cache_control = _rrugc_conditional_preview(
            tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
            size=size, revision=remote_id, requested_version=v,
            if_none_match=if_none_match,
        )
        if not_modified is not None:
            return not_modified
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail="Managed storage is unavailable")
    if thumbnail:
        compact = await _managed_drive_thumbnail_response(
            storage, tenant_id=principal.active_tenant_id, remote_file_id=remote_id,
            size_pixels=size, cache_control=cache_control,
            cache_version=job_id + ":" + remote_id, etag=etag,
        )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id, asset_id="rrugc-keyword-image:" + job_id,
            remote_file_id=remote_id, content_type=content_type, size_bytes=size_bytes,
        ))
    except StorageProviderError as exc:
        raise HTTPException(status_code=503 if exc.retryable else 502,
                            detail={"code": exc.code, "message": "Keyword output unavailable"}) from exc
    return StreamingResponse(
        stream.body, media_type=content_type or stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/stage4/completions")
def list_stage4_completions(
    cursor: str | None = Query(default=None, max_length=110),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Durable tenant-scoped completion feed across *all* Stage 4 plan pages.

    No cursor bootstraps the current high-water mark without emitting old
    notifications. 'start' is a sentinel for an empty initial history.
    """
    from uuid import UUID

    filters = (
        RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
        RrugcStage2JobModel.status == "completed",
        RrugcStage2JobModel.output_remote_file_id.is_not(None),
        RrugcStage2JobModel.completed_at.is_not(None),
    )

    def token(completed_at, job_id: str) -> str:
        return completed_at.isoformat() + "|" + job_id

    scope = principal.active_tenant_id + ":" + principal.user_id
    if cursor is None:
        latest = session.execute(
            select(RrugcStage2JobModel.completed_at, RrugcStage2JobModel.id)
            .where(*filters)
            .order_by(RrugcStage2JobModel.completed_at.desc(), RrugcStage2JobModel.id.desc())
            .limit(1)
        ).first()
        return {"scope": scope, "items": [], "cursor": (
            token(latest[0], latest[1]) if latest else "start"
        ), "has_more": False, "bootstrap": True}

    query = (
        select(RrugcStage2JobModel, RrugcSourcePlanModel.source_name)
        .join(RrugcSourcePlanModel, RrugcSourcePlanModel.id == RrugcStage2JobModel.source_plan_id)
        .where(*filters, RrugcSourcePlanModel.tenant_id == principal.active_tenant_id)
    )
    if cursor != "start":
        try:
            iso, job_id = cursor.rsplit("|", 1)
            UUID(job_id)
            at = datetime.fromisoformat(iso)
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail={
                "code": "stage4_notification_cursor_invalid",
                "message": "Invalid Stage 4 notification cursor.",
            }) from exc
        query = query.where(or_(
            RrugcStage2JobModel.completed_at > at,
            and_(RrugcStage2JobModel.completed_at == at, RrugcStage2JobModel.id > job_id),
        ))
    results = session.execute(query.order_by(
        RrugcStage2JobModel.completed_at.asc(),
        RrugcStage2JobModel.id.asc(),
    ).limit(limit + 1)).all()
    page = results[:limit]
    return {
        "scope": scope,
        "items": [{
            "id": job.id,
            "source_plan_id": job.source_plan_id,
            "source_name": name,
            "completed_at": job.completed_at.isoformat(),
        } for job, name in page],
        "cursor": token(page[-1][0].completed_at, page[-1][0].id) if page else cursor,
        "has_more": len(results) > limit,
        "bootstrap": False,
    }


@router.get("/stage4/active-jobs", response_model=list[Stage2JobResponse])
def list_stage4_active_jobs(
    cursor: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Tenant-scoped active queue across every source group (not just the current page)."""
    query = select(RrugcStage2JobModel).where(
        RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
        RrugcStage2JobModel.status.in_(("queued", "running")),
    )
    if cursor:
        query = query.where(RrugcStage2JobModel.id > cursor)
    rows = session.scalars(query.order_by(RrugcStage2JobModel.id).limit(limit)).all()
    return [_stage2_job(row, current_user_id=principal.user_id) for row in rows]


@router.get("/stage2-jobs", response_model=list[Stage2JobResponse])
def list_stage2_jobs(
    source_plan_id: list[str] | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=2000),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    source_plan_ids = list(dict.fromkeys(
        str(value or "").strip()
        for value in (source_plan_id or [])
        if str(value or "").strip()
    ))
    if len(source_plan_ids) > 250 or any(len(value) > 36 for value in source_plan_ids):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "stage2_source_plan_filter_invalid",
                "message": "Stage 2 job filtering accepts at most 250 source plan IDs.",
            },
        )
    stage4_rows = RrugcRepository(session).list_stage2_jobs(
        principal.active_tenant_id,
        source_plan_ids=source_plan_ids,
        limit=limit,
    )
    from app.modules.processing.model import ProcessingJobModel

    # Read retry schedule in one tenant-scoped query; the Stage 4 UI must
    # distinguish a provider-quota hold from an ordinary runnable queue.
    processing_ids = [
        row.processing_job_id
        for row in stage4_rows
        if row.status == "queued" and row.processing_job_id
    ]
    retry_schedule = dict(session.execute(
        select(ProcessingJobModel.id, ProcessingJobModel.next_attempt_at).where(
            ProcessingJobModel.tenant_id == principal.active_tenant_id,
            ProcessingJobModel.id.in_(processing_ids),
            ProcessingJobModel.status.in_(("pending", "retry")),
        )
    ).all()) if processing_ids else {}
    return [
        _stage2_job(
            row,
            current_user_id=principal.user_id,
            next_attempt_at=retry_schedule.get(row.processing_job_id),
        )
        for row in stage4_rows
    ]


@router.get(
    "/stage3/review-groups",
    response_model=Stage3ReviewGroupListResponse,
)
def list_stage3_review_groups(
    limit: int = Query(default=250, ge=1, le=500),
    page_size: int = Query(default=250, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=36),
    folder_path: str | None = Query(default=None, max_length=512),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.loox_review_export import stage5_sample_email, stage5_sample_review_date
    # Keyset pagination: the previous 5,000-record cap hid old outputs and
    # loaded thousands of rows on every 5-second UI refresh.
    # (created_at, id) gives an immutable, deterministic ordering.
    query = (
        session.query(RrugcStage2JobModel, RrugcSourcePlanModel)
        .join(
            RrugcSourcePlanModel,
            RrugcSourcePlanModel.id == RrugcStage2JobModel.source_plan_id,
        )
        .filter(
            RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
            RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
            RrugcStage2JobModel.status == "completed",
            RrugcStage2JobModel.output_remote_file_id.is_not(None),
            RrugcStage2JobModel.output_remote_folder_id.is_not(None),
        )
        .outerjoin(
            RrugcStage3AnalysisModel,
            (RrugcStage3AnalysisModel.stage2_job_id == RrugcStage2JobModel.id)
            & (RrugcStage3AnalysisModel.tenant_id == principal.active_tenant_id),
        )
        .filter(or_(
            RrugcStage3AnalysisModel.id.is_(None),
            RrugcStage3AnalysisModel.status != "hidden",
        ))
    )
    # Global counts are independent of the keyset page. Stage 5 can display
    # 250 loaded images while more completed Stage 4 outputs exist in storage.
    summary_row = query.with_entities(
        func.count(RrugcStage2JobModel.id),
        func.count(func.distinct(RrugcStage2JobModel.output_remote_folder_id)),
        func.count(RrugcStage2JobModel.id).filter(RrugcStage3AnalysisModel.status == "ready"),
        func.count(RrugcStage2JobModel.id).filter(RrugcStage3AnalysisModel.status == "rejected"),
        func.count(RrugcStage2JobModel.id).filter(
            RrugcStage3AnalysisModel.status.in_(("queued", "analyzing"))
        ),
        func.count(RrugcStage2JobModel.id).filter(RrugcStage3AnalysisModel.status == "error"),
    ).one()
    total_count, folder_count, ready_count, rejected_count, analyzing_count, error_count = (
        int(value or 0) for value in summary_row
    )
    summary = Stage3ReviewSummaryResponse(
        total_groups=folder_count,
        total_images=total_count,
        ready_images=ready_count,
        rejected_images=rejected_count,
        analyzing_images=analyzing_count,
        pending_images=max(0, total_count - ready_count - rejected_count - analyzing_count - error_count),
        error_images=error_count,
    )
    # Return all folder counts separately from the paginated image payload.
    # Only path, Drive folder id and count are fetched, not image thumbnails.
    folder_rows = query.with_entities(
        RrugcSourcePlanModel.source_relative_path,
        RrugcStage2JobModel.output_remote_folder_id,
        func.count(RrugcStage2JobModel.id),
    ).group_by(
        RrugcSourcePlanModel.source_relative_path,
        RrugcStage2JobModel.output_remote_folder_id,
    ).all()
    folder_totals: dict[tuple[str, str], int] = {}
    for source_path, output_folder_id, count in folder_rows:
        folder = _rrugc_source_folder(source_path)
        key = (folder, str(output_folder_id))
        folder_totals[key] = folder_totals.get(key, 0) + int(count)
    folder_counts = [Stage3ReviewFolderCountResponse(path=path, folder_id=folder_id, image_count=count)
                     for (path, folder_id), count in sorted(folder_totals.items())]

    # Folder selection runs at SQL level before keyset pagination. Matching
    # includes descendants but never similarly prefixed sibling folders.
    if folder_path is not None:
        selected_path = folder_path.replace('\\', '/').strip('/')
        source_path_sql = func.replace(RrugcSourcePlanModel.source_relative_path, '\\', '/')
        if selected_path:
            query = query.filter(source_path_sql.startswith(selected_path + '/', autoescape=True))
        else:
            query = query.filter(~source_path_sql.contains('/'))
    if cursor:
        anchor = query.filter(RrugcStage2JobModel.id == cursor).first()
        if anchor is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "stage5_cursor_invalid",
                        "message": "The Stage 5 page cursor is no longer valid."},
            )
        anchor_job = anchor[0]
        query = query.filter(or_(
            RrugcStage2JobModel.created_at < anchor_job.created_at,
            and_(
                RrugcStage2JobModel.created_at == anchor_job.created_at,
                RrugcStage2JobModel.id < anchor_job.id,
            ),
        ))
    effective_size = min(limit, page_size)
    page_rows = query.order_by(
        RrugcStage2JobModel.created_at.desc(),
        RrugcStage2JobModel.id.desc(),
    ).limit(effective_size + 1).all()
    has_more = len(page_rows) > effective_size
    rows = page_rows[:effective_size]
    next_cursor = rows[-1][0].id if has_more else None

    stage2_ids = [job.id for job, _plan in rows]
    analyses_by_job: dict[str, RrugcStage3AnalysisModel] = {}
    if stage2_ids:
        analyses = session.scalars(
            select(RrugcStage3AnalysisModel).where(
                RrugcStage3AnalysisModel.tenant_id
                == principal.active_tenant_id,
                RrugcStage3AnalysisModel.stage2_job_id.in_(stage2_ids),
            )
        ).all()
        analyses_by_job = {row.stage2_job_id: row for row in analyses}

    from app.modules.realistic_review_ugc.stage5_before_source import stage5_before_references

    grouped: dict[str, dict[str, object]] = {}
    totals = Counter()
    for job, plan in rows:
        folder_id = str(job.output_remote_folder_id or "").strip()
        remote_file_id = str(job.output_remote_file_id or "").strip()
        if not folder_id or not remote_file_id:
            continue

        relative_path = (
            str(plan.source_relative_path or plan.source_name or "")
            .replace("\\", "/")
            .strip("/")
        )
        folder_path = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
        folder_name = folder_path.rsplit("/", 1)[-1] if folder_path else "Root folder"

        group = grouped.get(folder_id)
        if group is None:
            if len(grouped) >= limit:
                continue
            group = {
                "folder_id": folder_id,
                "folder_name": folder_name,
                "folder_path": folder_path,
                "latest_completed_at": job.completed_at,
                "images": [],
                "counts": Counter(),
            }
            grouped[folder_id] = group

        analysis = analyses_by_job.get(job.id)
        raw_status = str(analysis.status if analysis else "pending")
        analysis_status = (
            raw_status
            if raw_status
            in {"pending", "queued", "analyzing", "ready", "rejected", "error"}
            else "pending"
        )
        counts = group["counts"]
        assert isinstance(counts, Counter)
        counts[analysis_status] += 1
        totals[analysis_status] += 1

        images = group["images"]
        assert isinstance(images, list)
        images.append(
            Stage3ReviewImageResponse(
                stage2_job_id=job.id,
                source_plan_id=job.source_plan_id,
                source_name=plan.source_name,
                source_relative_path=plan.source_relative_path,
                output_remote_file_id=remote_file_id,
                output_width=job.output_width,
                output_height=job.output_height,
                output_content_type=job.output_content_type,
                completed_at=job.completed_at,
                preview_url=(
                    "/api/v1/realistic-review-ugc/stage2-jobs/"
                    + job.id
                    + "/output?thumbnail=true&size=512&v="
                    + remote_file_id
                ),
                original_url=(
                    "/api/v1/realistic-review-ugc/stage2-jobs/"
                    + job.id
                    + "/output"
                ),
                before_url=(
                    "/api/v1/realistic-review-ugc/stage2-jobs/" + job.id + "/reference-image"
                    if stage5_before_references(job) else None
                ),
                before_urls=[
                    "/api/v1/realistic-review-ugc/stage2-jobs/"
                    + job.id + "/reference-image?thumbnail=true&size=1024&reference_index=" + str(index)
                    + "&v=" + _reference[0]
                    for index, _reference in enumerate(stage5_before_references(job))
                ],
                analysis_id=analysis.id if analysis else None,
                analysis_status=analysis_status,
                final_score=analysis.final_score if analysis else None,
                mobile_ugc_score=(
                    analysis.mobile_ugc_score if analysis else None
                ),
                photorealism_score=(
                    analysis.photorealism_score if analysis else None
                ),
                product_visibility_score=(
                    analysis.product_visibility_score if analysis else None
                ),
                review_fit_score=(
                    analysis.review_fit_score if analysis else None
                ),
                person_visible=analysis.person_visible if analysis else None,
                hat_visible=analysis.hat_visible if analysis else None,
                product_visible=analysis.product_visible if analysis else None,
                embroidery_visible=(
                    analysis.embroidery_visible if analysis else None
                ),
                scene_type=analysis.scene_type if analysis else None,
                framing_type=analysis.framing_type if analysis else None,
                summary=analysis.summary if analysis else None,
                reviewer_name=analysis.reviewer_name if analysis else None,
                star_rating=analysis.star_rating if analysis else None,
                review_text=analysis.review_text if analysis else None,
                review_generated_at=(
                    analysis.review_generated_at if analysis else None
                ),
                sample_email=(stage5_sample_email(job.id, analysis.reviewer_name) if analysis and analysis.reviewer_name and analysis.review_text else None),
                sample_review_date=(stage5_sample_review_date(analysis.id, analysis.review_generated_at or job.completed_at or analysis.created_at) if analysis and analysis.reviewer_name and analysis.review_text else None),
                reject_reasons=(
                    list(analysis.reject_reasons_json or [])
                    if analysis
                    else []
                ),
                last_error_code=(
                    analysis.last_error_code if analysis else None
                ),
            )
        )

    items: list[Stage3ReviewGroupResponse] = []
    for group in grouped.values():
        images = group["images"]
        counts = group["counts"]
        assert isinstance(images, list)
        assert isinstance(counts, Counter)
        image_count = len(images)
        ready_count = counts["ready"]
        rejected_count = counts["rejected"]
        analyzing_count = counts["queued"] + counts["analyzing"]
        pending_count = counts["pending"]
        error_count = counts["error"]

        if analyzing_count:
            group_status = "analyzing"
        elif image_count and ready_count == image_count:
            group_status = "ready"
        elif ready_count:
            group_status = "partial"
        elif image_count and rejected_count == image_count:
            group_status = "rejected"
        elif error_count and not pending_count:
            group_status = "error"
        else:
            group_status = "pending"

        items.append(
            Stage3ReviewGroupResponse(
                folder_id=str(group["folder_id"]),
                folder_name=str(group["folder_name"]),
                folder_path=str(group["folder_path"]),
                image_count=image_count,
                status=group_status,
                ready_count=ready_count,
                rejected_count=rejected_count,
                analyzing_count=analyzing_count,
                pending_count=pending_count,
                error_count=error_count,
                latest_completed_at=group["latest_completed_at"],
                images=images,
            )
        )

    return Stage3ReviewGroupListResponse(
        summary=summary,
        folder_counts=folder_counts,
        items=items,
        total_groups=len(items),
        total_images=sum(len(item.images) for item in items),
        has_more=has_more,
        next_cursor=next_cursor,
        ready_images=totals["ready"],
        rejected_images=totals["rejected"],
        analyzing_images=totals["queued"] + totals["analyzing"],
        pending_images=totals["pending"],
        error_images=totals["error"],
    )


@router.delete("/stage3/reviews/{stage2_job_id}")
def hide_stage5_review(
    stage2_job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Hide a review concept; preserve the Stage 4 output and its history."""
    import logging

    review = session.scalar(select(RrugcStage3AnalysisModel).where(
        RrugcStage3AnalysisModel.tenant_id == principal.active_tenant_id,
        RrugcStage3AnalysisModel.stage2_job_id == stage2_job_id,
    ).with_for_update())
    if review is None:
        raise HTTPException(status_code=404, detail={
            "code": "stage5_review_not_found", "message": "Review not found.",
        })
    if review.status not in {"ready", "rejected", "error", "hidden"}:
        raise HTTPException(status_code=409, detail={
            "code": "stage5_review_still_processing",
            "message": "Wait until this review finishes analyzing before hiding it.",
        })
    if review.status != "hidden":
        review.status = "hidden"
        session.commit()
        logging.getLogger(__name__).info("stage5_review_hidden", extra={
            "stage2_job_id": stage2_job_id,
            "tenant_id": principal.active_tenant_id,
            "user_id": principal.user_id,
        })
    return {"hidden": True, "stage2_job_id": stage2_job_id}


@router.api_route("/stage5/public-photo/{stage2_job_id}.{extension}", methods=["GET", "HEAD"])
async def public_stage5_original_photo(
    request: Request,
    stage2_job_id: str,
    extension: str,
    session: Session = Depends(get_db),
):
    """Share the full-resolution Stage 4 output only while its Stage 5 concept is ready.

    UUID identifiers are unguessable bearer links. Hiding the concept revokes
    access (subject to the short public cache TTL). Nothing on Google Drive
    is made public or assigned public Drive sharing permissions.
    """
    from uuid import UUID
    from io import BytesIO
    from PIL import Image

    try:
        job_id = str(UUID(stage2_job_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Photo unavailable.")
    if extension not in {"jpg", "png"}:
        raise HTTPException(status_code=404, detail="Photo unavailable.")
    def load_photo_metadata() -> tuple[str, str, str | None, int | None, str]:
        # This is a synchronous SQLAlchemy Session; never block the ASGI event
        # loop waiting for a QueuePool checkout under concurrent photo traffic.
        # Return immutable scalar values and ALWAYS release the DB transaction,
        # including HEAD/404 paths, before remote OAuth/open/stream I/O begins.
        try:
            record = session.execute(
                select(RrugcStage2JobModel, RrugcStage3AnalysisModel)
                .join(
                    RrugcStage3AnalysisModel,
                    (RrugcStage3AnalysisModel.stage2_job_id == RrugcStage2JobModel.id)
                    & (RrugcStage3AnalysisModel.tenant_id == RrugcStage2JobModel.tenant_id),
                )
                .where(
                    RrugcStage2JobModel.id == job_id,
                    RrugcStage2JobModel.status == "completed",
                    RrugcStage2JobModel.output_remote_file_id.is_not(None),
                    RrugcStage3AnalysisModel.status == "ready",
                )
            ).first()
            if record is None:
                raise HTTPException(status_code=404, detail="Photo unavailable.")
            job, _review = record
            original_type = str(job.output_content_type or "image/png").lower()
            expected_extension = "jpg" if original_type in {"image/jpeg", "image/jpg"} else "png"
            if extension != expected_extension:
                raise HTTPException(status_code=404, detail="Photo unavailable.")
            return (
                str(job.tenant_id), str(job.output_remote_file_id),
                job.output_content_type, job.output_size_bytes, original_type,
            )
        finally:
            session.close()

    tenant_id, remote_file_id, content_type, size_bytes, original_type = (
        await run_in_threadpool(load_photo_metadata)
    )

    if request.method == "HEAD":
        return Response(status_code=200, media_type="image/jpeg" if extension == "jpg" else "image/png",
                        headers={
                            "Cache-Control": "public, max-age=300",
                            "X-Content-Type-Options": "nosniff",
                        })

    try:
        stored = await build_managed_storage_provider(get_settings()).open_asset(
            OpenStoredAssetInput(
                tenant_id=tenant_id,
                asset_id=f"rrugc-stage2:{job_id}",
                remote_file_id=remote_file_id,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )
    except (StorageProviderError, RuntimeError):
        raise HTTPException(status_code=503, detail="Original photo temporarily unavailable.")

    response_headers = {
        "Cache-Control": "public, max-age=300",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="review-{job_id}.{extension}"',
    }
    actual_type = str(stored.content_type or original_type).split(";", 1)[0].lower()
    if ((extension == "png" and actual_type == "image/png")
            or (extension == "jpg" and actual_type in {"image/jpeg", "image/jpg"})):
        return StreamingResponse(
            stored.body,
            media_type="image/jpeg" if extension == "jpg" else "image/png",
            headers=response_headers,
            background=BackgroundTask(stored.close),
        )
    # Loox supports PNG/JPG, but Stage 4 can also produce WebP.
    # PNG preserves every pixel at its original dimensions (no thumbnail resize).
    try:
        encoded = bytearray()
        async for chunk in stored.body:
            encoded.extend(chunk)
            if len(encoded) > 30_000_000:
                raise ValueError("Source image too large")
        with Image.open(BytesIO(encoded)) as source:
            converted = BytesIO()
            is_jpeg = extension == "jpg"
            source.convert(
                "RGB" if is_jpeg else ("RGBA" if "A" in source.getbands() else "RGB")
            ).save(
                converted, format="JPEG" if is_jpeg else "PNG",
                **({"quality": 98, "subsampling": 0} if is_jpeg else {}),
            )
        return Response(
            content=converted.getvalue(),
            media_type="image/jpeg" if extension == "jpg" else "image/png",
            headers=response_headers,
        )
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="Original photo temporarily unavailable.")
    finally:
        await stored.close()


@router.get("/stage3/reviews/export-loox")
def export_stage5_loox_review_concepts(
    format: str = Query(default="csv", pattern="^(csv|xlsx|internal_csv)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Export all eligible synthetic concepts, not just the loaded UI page."""
    from app.modules.realistic_review_ugc.loox_review_export import (
        PREFIX, build_loox_xlsx, build_loox_csv, build_internal_concept_csv,
        clean_concept_copy, loox_original_photo_url, shopify_handle_from_folder, loox_public_photo_url,
        stage5_sample_email, stage5_sample_review_date, format_stage5_created_at,
    )
    joined = session.execute(
        select(RrugcStage3AnalysisModel, RrugcStage2JobModel, RrugcSourcePlanModel)
        .join(RrugcStage2JobModel, RrugcStage2JobModel.id == RrugcStage3AnalysisModel.stage2_job_id)
        .join(RrugcSourcePlanModel, RrugcSourcePlanModel.id == RrugcStage2JobModel.source_plan_id)
        .where(
            RrugcStage3AnalysisModel.tenant_id == principal.active_tenant_id,
            RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
            RrugcSourcePlanModel.tenant_id == principal.active_tenant_id,
            RrugcStage3AnalysisModel.status == "ready",
            RrugcStage2JobModel.status == "completed",
            RrugcStage2JobModel.output_remote_file_id.is_not(None),
            RrugcStage3AnalysisModel.review_text.is_not(None),
            RrugcStage3AnalysisModel.reviewer_name.is_not(None),
            RrugcStage3AnalysisModel.star_rating.between(1, 5),
        )
        .order_by(RrugcStage3AnalysisModel.created_at.asc(), RrugcStage3AnalysisModel.id.asc())
    )
    rows = []
    used_sample_dates: set[str] = set()
    missing_photos = 0
    normalized_handles = 0
    for review, image, plan in joined:
        raw_path = str(plan.source_relative_path or "")
        folder = raw_path.replace("\\", "/").strip("/").split("/", 1)[0].strip()
        product_handle = shopify_handle_from_folder(folder)
        if product_handle != folder:
            normalized_handles += 1
        # Do not invent a Shopify mapping: a sanitized slug is a *candidate*
        # handle and the merchant must match it to the actual Shopify URL.
        # Never leak private CAM/Drive URLs to a storefront importer.
        photo_url = loox_public_photo_url(
            loox_original_photo_url(image.id, image.output_content_type)
        ) if image.output_remote_file_id else ""
        if not photo_url:
            missing_photos += 1
        created = review.review_generated_at or image.completed_at
        created_at_value = format_stage5_created_at(created)
        if format == "xlsx":
            # Preview/Test only: use the unique review ID, not the shared image ID.
            # Rehash rare collisions so rows have unique sample timestamps;
            # deterministic order ensures identical re-exports.
            stable_anchor = created or review.created_at or image.created_at
            for nonce in range(100_000):
                candidate = stage5_sample_review_date(review.id, stable_anchor, nonce=nonce)
                if candidate and candidate not in used_sample_dates:
                    created_at_value = candidate
                    used_sample_dates.add(candidate)
                    break
            else:
                raise HTTPException(status_code=422, detail="Unable to allocate distinct sample timestamps.")
        rows.append((
            product_handle,
            int(review.star_rating),
            clean_concept_copy(review.reviewer_name, author=True),
            stage5_sample_email(image.id, review.reviewer_name),  # Name-based, non-deliverable test email.
            clean_concept_copy(review.review_text),
            created_at_value,
            photo_url,
            "",  # No verified order information exists for synthetic concepts.
        ))
    is_csv = format == "csv"
    return Response(
        content=(build_loox_csv([
            (*row[:4], PREFIX + row[4], *row[5:])
            for row in rows
        ]) if is_csv else (build_internal_concept_csv(rows) if format == "internal_csv" else build_loox_xlsx(rows))),
        media_type=("text/csv; charset=utf-8" if format in ("csv", "internal_csv") else
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": ('attachment; filename="CAM_Stage5_AI_Concepts_Loox.csv"' if is_csv else
                                    ('attachment; filename="CAM_Stage5_Internal_Concepts.csv"' if format == "internal_csv" else
                                     'attachment; filename="CAM_Stage5_AI_Concepts_Preview.xlsx"')),
            "Cache-Control": "private, no-store",
            "X-CAM-Review-Origin": "AI-generated-concepts-not-customer-testimonials",
            "X-CAM-Export-Sample-Emails": str(sum(1 for row in rows if row[3])),
            "X-CAM-Export-Rows": str(len(rows)),
            "X-CAM-Export-Missing-Photos": str(missing_photos),
            "X-CAM-Export-Normalized-Handles": str(normalized_handles),
            "Access-Control-Expose-Headers": "X-CAM-Export-Rows, X-CAM-Export-Missing-Photos, X-CAM-Export-Normalized-Handles, X-CAM-Review-Origin, X-CAM-Export-Sample-Emails",
        },
    )


@router.post(
    "/stage3/review-groups/analyze",
    response_model=Stage3AnalyzeResponse,
    status_code=202,
)
def analyze_stage3_review_groups(
    request: Stage3AnalyzeRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    # Analyze only unfinished/stale outputs. A capped query over *all* outputs
    # would repeatedly select the newest 500 and starve older unprocessed ones.
    query = session.query(RrugcStage2JobModel).outerjoin(
        RrugcStage3AnalysisModel,
        (RrugcStage3AnalysisModel.stage2_job_id == RrugcStage2JobModel.id)
        & (RrugcStage3AnalysisModel.tenant_id == principal.active_tenant_id),
    ).filter(
        RrugcStage2JobModel.tenant_id == principal.active_tenant_id,
        RrugcStage2JobModel.status == "completed",
        RrugcStage2JobModel.output_remote_file_id.is_not(None),
        RrugcStage2JobModel.output_remote_folder_id.is_not(None),
        or_(
            RrugcStage3AnalysisModel.id.is_(None),
            RrugcStage3AnalysisModel.status != "hidden",
        ),
    )
    if request.folder_id:
        query = query.filter(
            RrugcStage2JobModel.output_remote_folder_id == request.folder_id
        )
    if not request.force:
        query = query.filter(or_(
            RrugcStage3AnalysisModel.id.is_(None),
            RrugcStage3AnalysisModel.status.in_(("error", "failed")),
            RrugcStage3AnalysisModel.analysis_version != STAGE3_ANALYSIS_VERSION,
            RrugcStage3AnalysisModel.output_content_hash.is_distinct_from(
                RrugcStage2JobModel.output_content_hash
            ),
        ))
    eligible_count = query.count()
    rows = query.order_by(
        RrugcStage2JobModel.completed_at.asc(),
        RrugcStage2JobModel.created_at.asc(),
        RrugcStage2JobModel.id.asc(),
    ).limit(500).all()

    service = RrugcStage3Service(session)
    queued = 0
    for row in rows:
        _analysis, created = service.ensure_analysis_for_job(
            tenant_id=principal.active_tenant_id,
            stage2_job=row,
            force=request.force,
        )
        if created:
            queued += 1
    session.commit()
    return Stage3AnalyzeResponse(
        eligible=eligible_count,
        queued=queued,
        existing=max(0, len(rows) - queued),
        remaining=max(0, eligible_count - len(rows)),
        has_more=eligible_count > len(rows),
    )


class CodexAccountSettingsRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    enabled: bool
    max_concurrent: int = Field(ge=1, le=3)
    stages: list[str] = Field(default_factory=list, max_length=3)


@router.get("/codex-accounts")
def list_codex_accounts(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.codex_account_manager import status
    settings = get_settings()
    result = status(
        session, principal.active_tenant_id,
        settings.CODEX_IMAGE_HOME,
        settings.CODEX_IMAGE_BINARY,
    )
    result["can_manage"] = bool(
        principal.platform_admin or "tenant_members.manage" in principal.effective_permissions
    )
    session.commit()
    return result


@router.put("/codex-accounts/{account_id}")
def save_codex_account_settings(
    account_id: str,
    request: CodexAccountSettingsRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    if not (principal.platform_admin or
            "tenant_members.manage" in principal.effective_permissions):
        raise HTTPException(status_code=403, detail={
            "code": "codex_account_admin_required",
            "message": "Tenant administrator permission is required to change Codex account settings.",
        })
    from app.modules.realistic_review_ugc.codex_account_manager import (
        update_settings, status,
    )
    settings = get_settings()
    try:
        update_settings(
            session,
            tenant_id=principal.active_tenant_id,
            account_id=account_id,
            actor=principal.user_id,
            display_name=request.display_name,
            enabled=request.enabled,
            max_concurrent=request.max_concurrent,
            stages=request.stages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = status(
        session, principal.active_tenant_id,
        settings.CODEX_IMAGE_HOME,
        settings.CODEX_IMAGE_BINARY,
    )
    result["can_manage"] = True
    session.commit()
    return result


class Stage4QueueControlResponse(BaseModel):
    paused: bool
    queued: int
    running: int


class Stage4QueueControlRequest(BaseModel):
    paused: bool


@router.get("/stage4/queue-control", response_model=Stage4QueueControlResponse)
def get_stage4_queue_control(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.stage4_queue_control import stage4_queue_status
    return stage4_queue_status(session, principal.active_tenant_id)


@router.post("/stage4/queue-control", response_model=Stage4QueueControlResponse)
def update_stage4_queue_control(
    request: Stage4QueueControlRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    from app.modules.realistic_review_ugc.stage4_queue_control import set_stage4_paused
    return set_stage4_paused(
        session, tenant_id=principal.active_tenant_id,
        user_id=principal.user_id, paused=request.paused,
    )


@router.post("/stage4/jobs/{job_id}/regenerate", response_model=Stage2JobResponse, status_code=202)
def regenerate_stage4_output(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcStage2Service(session).regenerate_completed_job(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            job_id=job_id,
        )
    except RrugcStage2Error as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return _stage2_job(row, current_user_id=principal.user_id)


@router.post("/stage4/jobs/{job_id}/retry", response_model=Stage2JobResponse, status_code=202)
def retry_stage4_generation(
    job_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcStage2Service(session).retry_failed_job(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            job_id=job_id,
        )
    except RrugcStage2Error as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return _stage2_job(row, current_user_id=principal.user_id)


@router.post(
    "/source-plans/{source_plan_id}/stage2-jobs",
    response_model=Stage2JobCreatedResponse,
    status_code=202,
)
def create_stage2_job(
    source_plan_id: str,
    request: Stage2JobCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, created = RrugcStage2Service(session).create_job(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            source_plan_id=source_plan_id,
            selected_candidate_ids=request.selected_candidate_ids,
            skill_source=request.skill_source,
            skill_id=request.skill_id,
            skill_name=request.skill_name,
            skill_version=request.skill_version,
            prompt=request.prompt,
        )
    except RrugcStage2Error as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2JobCreatedResponse(
        created=created,
        job=_stage2_job(row, current_user_id=principal.user_id),
    )


@router.post(
    "/source-plans/{source_plan_id}/stage2-jobs/cancel",
    response_model=Stage2JobsCancelledResponse,
)
def cancel_stage2_jobs(
    source_plan_id: str,
    request: Stage2JobsCancelRequest | None = None,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        rows = RrugcStage2Service(session).cancel_recent_batch(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            source_plan_id=source_plan_id,
            job_ids=request.job_ids if request else None,
        )
    except RrugcStage2Error as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return Stage2JobsCancelledResponse(
        cancelled=len(rows),
        job_ids=[row.id for row in rows],
    )


@router.get("/media-storage/status")
def rrugc_media_storage_status(
    principal: CurrentPrincipal = Depends(READ),
):
    """Safe tenant-reader health summary; never expose OAuth tokens or account IDs.

    Production image routes can return 503 even while output metadata lists 200.
    Surface managed-storage reconnect state so the UI does not suggest regenerating
    existing artwork. A successful OAuth status is not proof each Drive file exists.
    """
    from app.modules.storage.managed_oauth import managed_storage_oauth_status

    status = managed_storage_oauth_status(get_settings())
    root_ready = bool(status["root_folder_configured"])
    connected = bool(status["connected"] and not status["reconnect_required"])
    return {
        "status": "connected" if root_ready and connected else "reconnect_required",
        "root_folder_configured": root_ready,
        "connected": connected,
        "reconnect_required": not (root_ready and connected),
        "retry_after_seconds": 60,
    }


@router.get("/stage2-jobs/{stage2_job_id}/reference-image")
@router.get("/stage2-jobs/{stage2_job_id}/source-image", include_in_schema=False)
async def get_stage5_before_reference_image(
    stage2_job_id: str,
    reference_index: int = Query(default=0, ge=0, le=2),
    thumbnail: bool = Query(default=False),
    size: int = Query(default=1024, ge=128, le=1024),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """The exact saved Pinterest photo used for the Stage 4 hat-swap.

    Keep the legacy source-image route as a safe alias (not a hat photo),
    while new clients use a different URL to bypass cached old image bytes.
    """
    from app.modules.realistic_review_ugc.stage5_before_source import stage5_before_references

    job = RrugcRepository(session).get_stage2_job(
        principal.active_tenant_id, stage2_job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Stage 4 job not found")
    references = stage5_before_references(job)
    if reference_index >= len(references):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "stage5_pinterest_reference_unavailable",
                "message": "The original Pinterest photo was not saved for this job.",
            },
        )
    remote_file_id, content_type, size_bytes = references[reference_index]
    job_id = job.id
    session.close()
    if thumbnail is True:
        not_modified, etag, cache_control = _rrugc_conditional_preview(
            tenant_id=principal.active_tenant_id, remote_file_id=remote_file_id,
            size=size, revision=remote_file_id, requested_version=v,
            if_none_match=if_none_match,
        )
        if not_modified is not None:
            return not_modified

    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={"code": "managed_storage_unavailable",
                    "message": "Managed Google Drive is unavailable."},
        )
    if thumbnail is True:
        compact = await _managed_drive_thumbnail_response(
            storage, tenant_id=principal.active_tenant_id,
            remote_file_id=remote_file_id, size_pixels=size,
            cache_control=cache_control,
            cache_version=f"{job_id}:{reference_index}:{remote_file_id}",
            etag=etag,
        )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id,
            asset_id=f"rrugc-stage4-pinterest-before:{job_id}:{reference_index}",
            remote_file_id=remote_file_id,
            content_type=content_type,
            size_bytes=size_bytes,
        ))
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={
                "code": "stage5_pinterest_reference_unavailable",
                "message": "The saved Pinterest photo could not be opened.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type or content_type or "image/jpeg",
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=3600", "Vary": "Cookie"},
    )


@router.get("/stage2-jobs/{stage2_job_id}/output")
async def get_stage2_job_output(
    stage2_job_id: str,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=192, ge=128, le=1024),
    v: str | None = Query(default=None, max_length=256),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    row = RrugcRepository(session).get_stage2_job(
        principal.active_tenant_id,
        stage2_job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Stage 2 job not found")
    if row.status != "completed" or not row.output_remote_file_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stage2_output_not_ready",
                "message": "Stage 2 generated output is not ready.",
            },
        )
    output_remote_file_id = row.output_remote_file_id
    output_content_type = row.output_content_type
    output_size_bytes = row.output_size_bytes
    row_id = row.id
    session.close()
    if thumbnail:
        not_modified, etag, cache_control = _rrugc_conditional_preview(
            tenant_id=principal.active_tenant_id,
            remote_file_id=output_remote_file_id,
            size=size, revision=output_remote_file_id,
            requested_version=v, if_none_match=if_none_match,
        )
        if not_modified is not None:
            return not_modified

    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    if thumbnail:
        compact = await _managed_drive_thumbnail_response(
            storage,
            tenant_id=principal.active_tenant_id,
            remote_file_id=output_remote_file_id,
            size_pixels=size,
            cache_control=cache_control,
            cache_version=f"{row_id}:{output_remote_file_id}",
            etag=etag,
        )
        if compact is not None:
            return compact
    try:
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=f"rrugc-stage2:{row_id}",
                remote_file_id=output_remote_file_id,
                content_type=output_content_type,
                size_bytes=output_size_bytes,
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 502,
            detail={
                "code": exc.code,
                "message": "Stage 2 generated output could not be opened.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=output_content_type or stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/source-plans/sync", response_model=SourcePlanSyncResponse)
async def sync_source_folder_plans(
    colorway_sources: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        result = await sync_source_plans(
            session,
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            queue_analysis=not colorway_sources,
        )
    except RrugcSourcePlanError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return SourcePlanSyncResponse(
        root_folder_id=result.root_folder_id,
        target_count=RRUGC_SOURCE_TARGET_COUNT,
        folders_scanned=result.folders_scanned,
        images_found=result.images_found,
        plans_created=result.plans_created,
        plans_updated=result.plans_updated,
        plans_missing=result.plans_missing,
        jobs_queued=result.jobs_queued,
        unchanged=result.unchanged,
    )


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
        row = RrugcService(session).refresh_campaign_discovery(row)
    except RrugcError as exc:
        raise _error(exc) from exc
    return _campaign(repository, row)


@router.post(
    "/campaigns/{campaign_id}/product-context/visual-analysis",
    response_model=CampaignResponse,
)
async def analyze_campaign_product_visual_context(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    if campaign.discovery_mode != "product_context":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "product_context_mode_required",
                "message": "Switch this campaign to Product context before analyzing product visuals.",
            },
        )
    if not campaign.product_id or not campaign.product_snapshot_json:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "campaign_product_required",
                "message": "Bind a product before analyzing product visuals.",
            },
        )
    if RrugcGenerationFoundation(session).binding_is_stale(campaign):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "campaign_product_binding_stale",
                "message": "Refresh the campaign product binding before analyzing product visuals.",
            },
        )

    reference_snapshot = list(campaign.product_reference_snapshot_json or [])
    selected_references = select_product_visual_references(
        reference_snapshot,
        limit=4,
    )
    if not selected_references:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "product_visual_references_required",
                "message": "No managed product reference images are available for visual context analysis.",
            },
        )

    settings = get_settings()
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )

    registry = build_ai_provider_registry(
        settings,
        session_factory=SessionLocal,
    )
    analyses = []
    failures: list[str] = []
    try:
        try:
            provider = registry.require("gemini")
        except AiProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": exc.code,
                    "message": "Gemini visual analysis is unavailable.",
                },
            ) from exc

        for snapshot in selected_references:
            reference_id = str(snapshot.get("id") or "").strip()
            if not reference_id:
                continue
            reference = repository.get_product_reference(
                principal.active_tenant_id,
                campaign.product_id,
                reference_id,
            )
            if reference is None or not reference.remote_file_id:
                failures.append("reference_unavailable")
                continue

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
                chunks: list[bytes] = []
                total = 0
                try:
                    async for chunk in stream.body:
                        total += len(chunk)
                        if total > PRODUCT_REFERENCE_MAX_BYTES:
                            raise ValueError("product_reference_too_large")
                        chunks.append(chunk)
                finally:
                    await stream.close()

                document, provider_name, model = await analyze_product_visual_reference(
                    provider=provider,
                    tenant_id=principal.active_tenant_id,
                    reference_id=reference.id,
                    image_bytes=b"".join(chunks),
                    image_mime_type=reference.content_type,
                    width=reference.width,
                    height=reference.height,
                    product_snapshot=dict(campaign.product_snapshot_json or {}),
                    view_type=reference.view_type,
                )
                analyses.append(
                    (document, snapshot, provider_name, model)
                )
            except StorageProviderError as exc:
                failures.append(exc.code)
            except AiProviderError as exc:
                failures.append(exc.code)
            except ValueError:
                failures.append("visual_analysis_invalid_response")
    finally:
        await registry.aclose()

    if not analyses:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "product_visual_analysis_failed",
                "message": (
                    "Product visual context could not be analyzed."
                    + (
                        " Last failure: " + failures[-1] + "."
                        if failures
                        else ""
                    )
                ),
            },
        )

    visual_context = merge_product_visual_context(
        analyses,
        binding_fingerprint=product_visual_binding_fingerprint(
            dict(campaign.product_snapshot_json or {}),
            reference_snapshot,
        ),
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )
    profile = dict(campaign.product_context_json or {})
    profile["visual_context"] = visual_context
    campaign.product_context_json = profile
    service = RrugcService(session)
    row = service.refresh_campaign_discovery(campaign)
    service.enqueue_context_reanalysis(
        row,
        binding_fingerprint=str(
            visual_context.get("binding_fingerprint") or ""
        ),
        limit=24,
    )
    return _campaign(repository, row, include_keyword_health=True)


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
        skill_name, skill_manifest = _resolve_generation_skill(
            campaign,
            request.worker_skill_version,
        )
    except CodexImageProviderError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    if skill_manifest is not None and request.reference_set_id:
        reference_set = repository.get_reference_set(
            principal.active_tenant_id,
            request.reference_set_id,
        )
        if reference_set is not None:
            items = repository.list_reference_set_items(
                principal.active_tenant_id,
                reference_set.id,
            )
            missing_roles = skill_manifest.missing_reference_roles(
                [item.role for item in items]
            )
            if missing_roles:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reference_set_skill_roles_missing",
                        "message": (
                            "Reference set is missing required roles for $"
                            + skill_manifest.skill_name
                            + ": "
                            + ", ".join(missing_roles)
                            + "."
                        ),
                    },
                )
            if len(items) > skill_manifest.max_references:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reference_set_skill_limit_exceeded",
                        "message": (
                            "Reference set exceeds the "
                            + str(skill_manifest.max_references)
                            + "-reference limit for $"
                            + skill_manifest.skill_name
                            + "."
                        ),
                    },
                )

    try:
        row, created = RrugcGenerationFoundation(session).prepare_attempt(
            campaign=campaign,
            candidate=candidate,
            user_id=principal.user_id,
            generation_variant=request.generation_variant,
            worker_skill_version=skill_name,
            reference_set_id=request.reference_set_id,
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
    attempt_id_value = attempt.id
    output_remote_file_id = attempt.output_remote_file_id
    output_content_type = attempt.output_content_type
    output_size_bytes = attempt.output_size_bytes
    # Generated media is streamed from managed storage. Release PostgreSQL
    # before remote I/O so slow clients cannot hold an API pool connection.
    session.close()
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
                asset_id=f"rrugc-generation:{attempt_id_value}",
                remote_file_id=output_remote_file_id,
                content_type=output_content_type,
                size_bytes=output_size_bytes,
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
        media_type=output_content_type or stream.content_type,
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
    campaign = _require_campaign(
        repository,
        principal.active_tenant_id,
        campaign_id,
    )
    product_context = _ranking_product_context(campaign)
    return [
        _candidate(row, product_context=product_context)
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
        row = RrugcService(session).reanalyze_candidate(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return AnalyzeResponse(
        candidate=_candidate(
            row,
            product_context=_ranking_product_context(campaign),
        )
    )


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
        candidate=_candidate(
            row,
            product_context=_ranking_product_context(campaign),
        ),
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
            profile_key=body.profile_key,
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
            profile_key=body.profile_key,
        )
    )
    return CandidateReferenceFeedbackResponse(
        candidate=_candidate(
            row,
            product_context=_ranking_product_context(campaign),
        ),
        learning=ReferencePreferenceLearningResponse(
            active=learning.active,
            good_count=learning.good_count,
            bad_count=learning.bad_count,
        ),
    )


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/context-feedback",
    response_model=CandidateContextFeedbackResponse,
)
def mark_candidate_context_feedback(
    campaign_id: str,
    candidate_id: str,
    body: CandidateContextFeedbackRequest,
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
        row = RrugcService(session).mark_candidate_context_label(
            candidate,
            label=body.label,
            note=body.note,
            user_id=principal.user_id,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return CandidateContextFeedbackResponse(
        candidate=_candidate(
            row,
            product_context=_ranking_product_context(campaign),
        )
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
        row = RrugcService(session).queue_candidate_import(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return ImportResponse(
        candidate=_candidate(
            row,
            product_context=_ranking_product_context(campaign),
        )
    )


@router.get(
    "/health",
    response_model=RrugcHealthResponse,
)
def rrugc_health(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    snapshot = RrugcMaintenanceService(session, get_settings()).health(
        principal.active_tenant_id
    )
    return RrugcHealthResponse(**asdict(snapshot))


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


@router.post(
    "/scout-agents/{agent_id}/reset-pairing",
    response_model=ScoutAgentCreatedResponse,
)
def reset_scout_agent_pairing(
    agent_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row, raw_token = RrugcAutoScoutService(session).reset_agent_pairing(
            tenant_id=principal.active_tenant_id,
            agent_id=agent_id,
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


@router.post(
    "/scout-agents/{agent_id}/logs",
    response_model=ScoutLogBatchResponse,
)
def auto_scout_agent_logs(
    agent_id: str,
    request: ScoutLogBatchRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    token = _bearer_token(authorization)
    try:
        accepted, created = RrugcScoutLogService(session).ingest(
            agent_id=agent_id,
            raw_token=token,
            events=[item.model_dump() for item in request.events],
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return ScoutLogBatchResponse(
        accepted=accepted,
        created=created,
        retention_days=SCOUT_LOG_RETENTION_DAYS,
    )


@router.get(
    "/scout-agents/{agent_id}/diagnostics",
)
def auto_scout_agent_diagnostics(
    agent_id: str,
    authorization: str | None = Header(default=None),
    x_scout_version: str | None = Header(default=None, alias="X-Scout-Version"),
    session: Session = Depends(get_db),
):
    settings = get_settings()
    service = RrugcAutoScoutService(
        session,
        jev_scout_query_enabled=settings.JEV_SCOUT_QUERY_ENABLED,
        jev_mode=settings.JEV_MODE,
    )
    token = _bearer_token(authorization)
    try:
        return service.diagnostics(
            agent_id=agent_id,
            raw_token=token,
            client_version=x_scout_version,
        )
    except RrugcError as exc:
        raise _error(exc) from exc


@router.post(
    "/scout-agents/{agent_id}/claim",
    response_model=ScoutClaimResponse | None,
)
def auto_scout_agent_claim(
    agent_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_scout_version: str | None = Header(default=None, alias="X-Scout-Version"),
    x_scout_machine: str | None = Header(default=None, alias="X-Scout-Machine"),
    session: Session = Depends(get_db),
):
    settings = get_settings()
    service = RrugcAutoScoutService(
        session,
        jev_client=getattr(request.app.state, "jev_client", None),
        jev_scout_query_enabled=settings.JEV_SCOUT_QUERY_ENABLED,
        jev_mode=settings.JEV_MODE,
    )
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
    source_plan = session.scalar(
        select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == claim.campaign.tenant_id,
            RrugcSourcePlanModel.campaign_id == claim.campaign.id,
        )
    )
    repository = RrugcRepository(session)
    stage2_used_ids = repository.completed_stage2_reference_ids(
        claim.campaign.tenant_id,
        claim.campaign.id,
        limit=500,
    )
    query_performance = _scout_query_performance(
        session,
        tenant_id=claim.campaign.tenant_id,
        campaign_id=claim.campaign.id,
        stage2_used_ids=stage2_used_ids,
    )
    search_queries = _rank_scout_queries(search_queries, query_performance)

    # Sync tenant-wide recent Pinterest history into the local Scout before it
    # opens detail pages. The backend rejects exact tenant duplicates anyway, so
    # spending browser/Gemini time on these Pins has no value.
    recent_pin_rows = list(session.scalars(
        select(RrugcCandidateModel.pin_url)
        .where(
            RrugcCandidateModel.tenant_id == claim.campaign.tenant_id,
            RrugcCandidateModel.pin_url.is_not(None),
        )
        .order_by(RrugcCandidateModel.created_at.desc())
        .limit(5000)
    ))
    known_pin_urls: list[str] = []
    known_pin_keys: set[str] = set()
    for pin_url in recent_pin_rows:
        clean = str(pin_url or "").strip()
        key = clean.casefold()
        if not clean or key in known_pin_keys:
            continue
        known_pin_keys.add(key)
        known_pin_urls.append(clean)

    manual_label = (
        RrugcCandidateModel.ai_signal_json["reference_manual_label"].as_string()
    )
    usable_reference = or_(
        RrugcCandidateModel.ai_signal_json.is_(None),
        manual_label.is_(None),
        manual_label.notin_(("bad", "ai")),
    )
    seed_base = (
        select(RrugcCandidateModel)
        .where(
            RrugcCandidateModel.tenant_id == claim.campaign.tenant_id,
            RrugcCandidateModel.campaign_id == claim.campaign.id,
            RrugcCandidateModel.status.in_(ANALYSIS_APPROVED_STATUSES),
            RrugcCandidateModel.pin_url.is_not(None),
            RrugcCandidateModel.image_url.is_not(None),
            usable_reference,
        )
    )
    seed_rows = list(session.scalars(
        seed_base
        .order_by(
            RrugcCandidateModel.analyzed_at.desc(),
            RrugcCandidateModel.final_score.desc(),
            RrugcCandidateModel.created_at.desc(),
        )
        .limit(60)
    ))
    if stage2_used_ids:
        seed_rows.extend(session.scalars(
            seed_base.where(RrugcCandidateModel.id.in_(stage2_used_ids))
        ))
    seed_by_id = {row.id: row for row in seed_rows}

    def related_seed_priority(row: RrugcCandidateModel) -> tuple:
        signal = row.ai_signal_json if isinstance(row.ai_signal_json, dict) else {}
        manual_good = signal.get("reference_manual_label") == "good"
        learned = signal.get("reference_preference_adjustment")
        learned_score = (
            float(learned)
            if isinstance(learned, (int, float))
            else 0.0
        )
        analyzed_at = row.analyzed_at or row.created_at
        return (
            1 if row.id in stage2_used_ids else 0,
            1 if manual_good else 0,
            learned_score,
            float(row.final_score or 0.0),
            analyzed_at,
            row.id,
        )

    approved_related_seeds = sorted(
        seed_by_id.values(),
        key=related_seed_priority,
        reverse=True,
    )[:12]
    return ScoutClaimResponse(
        run=_scout_run_response(claim.run),
        campaign_id=claim.campaign.id,
        query=claim.campaign.query,
        search_queries=search_queries,
        query_performance=query_performance,
        known_pin_urls=known_pin_urls,
        target_count=claim.campaign.target_count,
        max_scroll_batches=claim.run.max_scroll_batches,
        auto_import=claim.campaign.auto_import,
        progress=claim.progress,
        pipeline_count=claim.pipeline_count,
        source_plan_id=source_plan.id if source_plan is not None else None,
        source_file_id=source_plan.source_file_id if source_plan is not None else None,
        source_relative_path=(
            source_plan.source_relative_path if source_plan is not None else None
        ),
        source_name=source_plan.source_name if source_plan is not None else None,
        source_context=dict(claim.campaign.product_context_json or {}) or None,
        related_seeds=[
            ScoutRelatedSeed(
                pin_url=row.pin_url,
                image_url=row.image_url,
                alt_text=row.alt_text,
            )
            for row in approved_related_seeds
        ],
    )


@router.post(
    "/scout-agents/{agent_id}/runs/{run_id}/candidates",
    response_model=AutoScoutCandidateBatchResponse,
)
def auto_scout_run_candidates(
    agent_id: str,
    run_id: str,
    request: AutoScoutCandidateBatchRequest,
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
            source_query=request.source_query,
        )
    except RrugcError as exc:
        raise _error(exc) from exc

    product_context = _ranking_product_context(row)
    return CandidateBatchResponse(
        created=created,
        existing=existing,
        items=[
            _candidate(candidate, product_context=product_context)
            for candidate in candidates
        ],
    )