from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.domain.providers.contracts import (
    AiProviderError,
    OpenStoredAssetInput,
    StorageProviderError,
)
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.image_generation.providers import GEMINI_IMAGE_MODEL
from app.modules.image_generation.service import provider_capability
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
    ScoutAgentHeartbeatRequest,
    ScoutClaimResponse,
    ScoutRunCompleteRequest,
    ScoutRunResponse,
    ScoutRelatedSeed,
    ScoutHeartbeatRequest,
    ScoutTaskResponse,
    SourcePlanReferencePreviewResponse,
    SourcePlanGroupImageResponse,
    SourcePlanResponse,
    SourcePlanPageResponse,
    SourcePlanSyncResponse,
)
from app.modules.realistic_review_ugc.review import RrugcReviewService
from app.modules.realistic_review_ugc.scout_automation import (
    RrugcAutoScoutService,
    effective_agent_status,
    keyword_health_rows,
    quality_pipeline_count,
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
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_TARGET_COUNT,
    RrugcSourcePlanError,
    sync_source_plans,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.ai.factory import build_ai_provider_registry
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
            f"?v={row.source_revision[:16]}"
        ),
        source_web_url=row.source_web_url,
        source_width=row.source_width,
        source_height=row.source_height,
        source_size_bytes=row.source_size_bytes,
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
            f"?v={row.source_revision[:16]}"
        ),
        source_revision=row.source_revision,
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


@router.get("/source-plans", response_model=SourcePlanPageResponse)
def get_source_plans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    sort_by: str = Query(
        default="source",
        pattern="^(source|updated|analyzed|group_size|status)$",
    ),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    all_plans = list(
        session.scalars(
            select(RrugcSourcePlanModel)
            .where(RrugcSourcePlanModel.tenant_id == principal.active_tenant_id)
            .order_by(
                RrugcSourcePlanModel.source_relative_path.asc(),
                RrugcSourcePlanModel.id.asc(),
            )
        )
    )

    grouped_plans: dict[str, list[RrugcSourcePlanModel]] = {}
    for source_plan in all_plans:
        group_key = (
            f"embroidery:{source_plan.embroidery_signature}"
            if source_plan.embroidery_signature
            else f"source:{source_plan.id}"
        )
        grouped_plans.setdefault(group_key, []).append(source_plan)

    needle = str(q or "").strip().lower()
    groups = list(grouped_plans.values())
    if needle:
        groups = [
            members
            for members in groups
            if any(
                needle in member.source_name.lower()
                or needle in member.source_relative_path.lower()
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
        else:
            primary = stable_path
        return primary, stable_path, stable_id

    groups.sort(
        key=group_sort_key,
        reverse=sort_dir == "desc",
    )

    total = len(groups)
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
                # Human-negative references remain durable training examples,
                # but never stay in the active real-reference picker.
                continue
            if candidate.status == "rejected_context":
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
            previews=previews_by_campaign.get(row.campaign_id or "", []),
            embroidery_group_size=len(group_members_by_plan_id.get(row.id, [row])),
            source_group_images=group_members_by_plan_id.get(row.id, [row]),
        )
        for row in plans
    ]
    return SourcePlanPageResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/source-plans/{source_plan_id}/image")
async def get_source_plan_image(
    source_plan_id: str,
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
            "ETag": f'"{source_revision}"',
        },
    )


@router.post("/source-plans/sync", response_model=SourcePlanSyncResponse)
async def sync_source_folder_plans(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        result = await sync_source_plans(
            session,
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
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
    source_plan = session.scalar(
        select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == claim.campaign.tenant_id,
            RrugcSourcePlanModel.campaign_id == claim.campaign.id,
        )
    )
    approved_related_seeds = list(
        session.scalars(
            select(RrugcCandidateModel)
            .where(
                RrugcCandidateModel.tenant_id == claim.campaign.tenant_id,
                RrugcCandidateModel.campaign_id == claim.campaign.id,
                RrugcCandidateModel.status.in_(ANALYSIS_APPROVED_STATUSES),
                RrugcCandidateModel.pin_url.is_not(None),
                RrugcCandidateModel.image_url.is_not(None),
            )
            .order_by(
                RrugcCandidateModel.analyzed_at.desc(),
                RrugcCandidateModel.final_score.desc(),
                RrugcCandidateModel.created_at.desc(),
            )
            .limit(12)
        )
    )
    return ScoutClaimResponse(
        run=_scout_run_response(claim.run),
        campaign_id=claim.campaign.id,
        query=claim.campaign.query,
        search_queries=search_queries,
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
