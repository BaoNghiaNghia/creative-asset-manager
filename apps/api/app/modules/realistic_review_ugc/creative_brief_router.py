"""Tenant-authorized Creative Brief API, separate from image generation endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.creative_brief_service import (
    INTERACTIVE_BRIEF_PRIORITY, get_brief, get_v3_preview, queue_brief, queue_picked_backfill, v3_enabled,
)

router = APIRouter(prefix="/keyword-creative-briefs", tags=["creative-brief"])
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")


@router.get("/v3/status")
def creative_engine_v3_status(principal: CurrentPrincipal = Depends(READ)):
    """Capability probe: HTTP 200 when OFF, no tenant identifiers or data."""
    return {"available": v3_enabled(get_settings(), principal.active_tenant_id),
            "schema_version": "creative-v3.0-preview-v1"}


@router.get("/v3/generation-status")
def creative_v3_generation_status(principal: CurrentPrincipal = Depends(READ)):
    """Read-only tenant capability probe, no worker or provider call."""
    from app.modules.realistic_review_ugc.creative_v3_generation import (
        SCHEMA, SLOT_COUNT, enabled,
    )
    from app.modules.realistic_review_ugc.heritage_lettering import (
        STYLE_POLICY_VERSION, resolve_style_policy,
    )
    default_style, default_version = resolve_style_policy()
    from app.modules.realistic_review_ugc.madeira_visor_threads import (
        PALETTE_VERSION, MADEIRA_VISOR_THREADS,
    )
    from app.modules.realistic_review_ugc.browser_image_mode import selectable_modes
    return {
        "image_engines": selectable_modes(),
        "thread_palette_version": PALETTE_VERSION,
        "verified_visor_thread_count": len(MADEIRA_VISOR_THREADS),
        "available": enabled(get_settings(), principal.active_tenant_id),
        "schema_version": SCHEMA, "output_count": SLOT_COUNT,
        "default_design_style": default_style,
        "default_style_version": default_version,
        "style_policy_version": STYLE_POLICY_VERSION,
        "applies_to": ["generate", "regenerate"],
        "retry_preserves_original_style": True,
    }


class CreativeV3GenerateRequest(BaseModel):
    expected_brief_updated_at: str = Field(min_length=10, max_length=70)
    # "auto" resolves the latest server-side style policy at fresh job queue
    # time. Retry reuses its original processing snapshot instead.
    design_style: str = Field(default="auto", pattern="^(auto|balanced|heritage|experimental)$")
    image_engine: str = Field(default="codex_cli", pattern="^(codex_cli|browser_extension)$")


@router.post("/{keyword_id}/v3-generate", status_code=202)
def creative_v3_generate(
    keyword_id: str,
    body: CreativeV3GenerateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Explicit, revision-bound nine-image run using the existing Stage 1 queue."""
    from app.modules.realistic_review_ugc.keyword_images import (
        KeywordImageError, KeywordImageService,
    )
    from app.modules.realistic_review_ugc.creative_v3_generation import SLOT_COUNT
    try:
        job = KeywordImageService(session, get_settings()).queue_creative_v3(
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            keyword_id=keyword_id,
            expected_brief_updated_at=body.expected_brief_updated_at,
            design_style=body.design_style,
            image_engine=body.image_engine,
        )
        return {"keyword_id": keyword_id, "job_id": job.id,
                "status": job.status, "queued": True,
                "engine": "creative-v3", "expected_output_count": SLOT_COUNT}
    except KeywordImageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail={
            "code": exc.code, "message": exc.message,
        }) from exc


class CreativeV3RegenerateRequest(CreativeV3GenerateRequest):
    confirm_regenerate: bool


@router.post("/{keyword_id}/v3-regenerate", status_code=202)
def creative_v3_regenerate(
    keyword_id: str,
    body: CreativeV3RegenerateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Explicit fresh nine-image run, independent of all prior checkpoints."""
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageError, KeywordImageService
    from app.modules.realistic_review_ugc.creative_v3_generation import SLOT_COUNT
    if body.confirm_regenerate is not True:
        raise HTTPException(status_code=422, detail="Confirm Regenerate before creating a new V3 batch.")
    try:
        job = KeywordImageService(session, get_settings()).queue_creative_v3(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            keyword_id=keyword_id,
            expected_brief_updated_at=body.expected_brief_updated_at,
            design_style=body.design_style,
            image_engine=body.image_engine,
            regenerate=True,
        )
        return {
            "keyword_id": keyword_id, "job_id": job.id,
            "processing_job_id": job.processing_job_id,
            "status": job.status, "queued": True, "action": "regenerate",
            "engine": "creative-v3", "expected_output_count": SLOT_COUNT,
        }
    except KeywordImageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail={
            "code": exc.code, "message": exc.message,
        }) from exc


class CreativeV3RetryRequest(CreativeV3GenerateRequest):
    expected_processing_job_id: str = Field(min_length=36, max_length=36)
    confirm_retry: bool


@router.post("/{keyword_id}/v3-retry", status_code=202)
def creative_v3_retry(
    keyword_id: str,
    body: CreativeV3RetryRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Resume a failed V3 ProcessingJob ID, preserving its verified checkpoints."""
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageError, KeywordImageService
    if body.confirm_retry is not True:
        raise HTTPException(status_code=422, detail="Confirm Retry before resuming a V3 attempt.")
    try:
        job = KeywordImageService(session, get_settings()).retry_creative_v3(
            tenant_id=principal.active_tenant_id,
            keyword_id=keyword_id,
            expected_brief_updated_at=body.expected_brief_updated_at,
            expected_processing_job_id=body.expected_processing_job_id,
        )
        from app.modules.processing.model import ProcessingJobModel
        processing = session.get(ProcessingJobModel, job.processing_job_id)
        return {
            "keyword_id": keyword_id, "job_id": job.id,
            "processing_job_id": job.processing_job_id,
            "status": job.status, "queued": True, "action": "retry",
            "engine": "creative-v3", "expected_output_count":
                (processing.payload_json or {}).get("expected_output_count", 6) if processing else 6,
        }
    except KeywordImageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail={
            "code": exc.code, "message": exc.message,
        }) from exc


@router.get("/{keyword_id}/v3-preview")
def creative_engine_v3_preview(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Tenant-scoped, non-generating V1-to-V3 concept preview for Style Lab."""
    if not v3_enabled(get_settings(), principal.active_tenant_id):
        raise HTTPException(status_code=404, detail="Creative Engine V3 unavailable")
    try:
        return get_v3_preview(
            session, tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Picked keyword not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Existing Creative Brief needs review") from exc


@router.get("/{keyword_id}")
def creative_brief_detail(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        return get_brief(
            session, tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Picked keyword not found") from exc


@router.post("/backfill", status_code=202)
def creative_brief_backfill(
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    retry_failed: bool = Query(default=False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    if not get_settings().RRUGC_CREATIVE_BRIEF_ENABLED:
        raise HTTPException(status_code=503, detail="Creative Brief is disabled.")
    result = queue_picked_backfill(
        session, tenant_id=principal.active_tenant_id,
        limit=limit, offset=offset, settings=get_settings(),
        retry_failed=retry_failed,
    )
    return result


@router.post("/{keyword_id}/generate", status_code=202)
def creative_brief_generate(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    if not get_settings().RRUGC_CREATIVE_BRIEF_ENABLED:
        raise HTTPException(status_code=503, detail="Creative Brief is disabled.")
    try:
        status, queued = queue_brief(
            session, tenant_id=principal.active_tenant_id,
            keyword_id=keyword_id, settings=get_settings(), retry=True,
            priority=INTERACTIVE_BRIEF_PRIORITY, actor_id=principal.user_id,
        )
        session.commit()
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="Picked keyword not found") from exc
    except Exception as exc:
        from app.modules.agent_harness.trial_admission import TrialAdmissionConflict
        session.rollback()
        if isinstance(exc, TrialAdmissionConflict):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return {"keyword_id": keyword_id, "status": status, "queued": queued}