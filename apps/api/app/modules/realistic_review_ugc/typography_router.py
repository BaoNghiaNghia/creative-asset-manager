"""G1.2 Typography Ideas Library API, off by default and strictly tenant-scoped.

No generation, AI analysis, Scout lease, or Stage 1 v1 behavior is exposed here.
Physical bytes continue to use the existing managed Reference Library.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4
import re
from urllib.parse import urlsplit
from typing import Literal
from pydantic import StrictInt

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.explorer.cache import CachedThumbnail, thumbnail_cache
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.authorization.principal import (
    CurrentPrincipal, require_authenticated_principal, require_permission,
)
from app.modules.realistic_review_ugc.model import (
    RrugcReferenceAssetModel, RrugcCampaignModel, RrugcKeywordVolumeModel,
)
from app.modules.realistic_review_ugc.reference_library import (
    REFERENCE_ASSET_MAX_BYTES, ReferenceLibraryError, RrugcReferenceLibrary,
    reference_image_content_type,
)
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel, RrugcTypographyStyleDnaModel,
    RrugcTypographyAnalysisJobModel,
)
from app.modules.realistic_review_ugc.typography_analysis_queue import TypographyAnalysisQueue
from app.modules.realistic_review_ugc.typography_thumbnails import (
    MAX_REFERENCE_THUMB_BYTES, typography_thumbnail_webp,
)
from app.modules.realistic_review_ugc.image_cache_policy import (
    preview_etag, matches_etag,
)
from app.modules.realistic_review_ugc.typography_dna_review import (
    TypographyDnaReviewRequest, TypographyReviewError, apply_human_review,
    effective_reviewed_dna, review_view, VERIFIED_RIGHTS,
)
from app.modules.realistic_review_ugc.typography_similarity import (
    is_typography_dhash, candidate_matches, typography_hamming_distance,
    has_stale_roi_hash,
)
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
from app.modules.realistic_review_ugc.service import RrugcError
from app.modules.realistic_review_ugc.typography_repository import (
    TypographyReferenceError, TypographyReferenceRepository,
)
from app.modules.realistic_review_ugc.typography_preview import (
    TypographyPreviewError, build_typography_previews,
)
from app.modules.realistic_review_ugc.typography_quality_gate import (
    TypographyQualityError, audit_svg_previews,
)
from app.modules.realistic_review_ugc.typography_generation_plan import (
    build_concept_short_v2_plan,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, enabled as pilot_enabled, resolve_plan as resolve_pilot_plan,
)
from app.modules.realistic_review_ugc.typography_reviewed_batch import inspect_reviewed_batch
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


READ = require_permission("typography_reference.read")
UPLOAD = require_permission("typography_reference.upload")
MANAGE = require_permission("typography_reference.manage")
PROFILE = "typography-idea"
# Server-owned analyzer version; never accept a client-controlled model/version.
G3_ANALYZER_VERSION = "g3-layout-font-overlap-v1"
MAX_BATCH_FILES = 12
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def _tenant_can_use_v2(tenant_id: str) -> bool:
    settings = get_settings()
    tenants = {
        tenant.strip()
        for tenant in settings.TYPOGRAPHY_V2_CANARY_TENANT_IDS.split(",")
        if tenant.strip()
    }
    return bool(settings.TYPOGRAPHY_V2_ENABLED and tenant_id in tenants)


def _g3_worker_enabled(tenant_id: str) -> bool:
    """Independent opt-in. The library feature alone never allows billed AI."""
    settings = get_settings()
    return bool(_tenant_can_use_v2(tenant_id)
                and getattr(settings, "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED", False)
                and getattr(settings, "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED", False))


def _gate(principal: CurrentPrincipal = Depends(require_authenticated_principal)):
    """Even an admin cannot reach this pilot API without an explicit tenant canary."""
    if not _tenant_can_use_v2(principal.active_tenant_id):
        raise HTTPException(status_code=404, detail={
            "code": "typography_v2_disabled", "message": "Typography V2 is not enabled.",
        })
    return principal


# A non-mutating, authenticated capability probe returns HTTP 200 when OFF.
# This prevents the default Stage 1 page from emitting expected 404s in Browser QA.
status_router = APIRouter(prefix="/typography", tags=["typography-v2"])


# Scout Design is a separate, explicit intake from the existing Windows Scout.
# This endpoint does not queue G3, approve rights, call ImageGen, or mutate Stage 1 V1.
DESIGN_SCOUT_DAILY_LIMIT = 40
DESIGN_SCOUT_BYTES_LIMIT = 8 * 1024 * 1024
_PIN_PATH = re.compile(r"/pin/([0-9]{5,25})/?\Z")


def _canonical_design_pin(value: str) -> tuple[str, str]:
    try:
        parts = urlsplit(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "typography_scout_source_invalid"}) from exc
    if (parts.scheme != "https" or parts.netloc.lower() not in {"pinterest.com", "www.pinterest.com"}):
        raise HTTPException(status_code=422, detail={"code": "typography_scout_source_invalid"})
    match = _PIN_PATH.fullmatch(parts.path)
    if match is None:
        raise HTTPException(status_code=422, detail={"code": "typography_scout_source_invalid"})
    return "https://www.pinterest.com/pin/" + match.group(1) + "/", match.group(1)


def _design_scout_agent(session: Session, *, agent_id: str, authorization: str | None):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail={"code": "typography_scout_auth_invalid"})
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=token.strip(),
        )
    except RrugcError as exc:
        raise HTTPException(status_code=401, detail={"code": "typography_scout_auth_invalid"}) from exc
    if not (_tenant_can_use_v2(agent.tenant_id)
            and getattr(get_settings(), "TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED", False) is True):
        raise HTTPException(status_code=404, detail={"code": "typography_design_scout_disabled"})
    return agent


@status_router.get("/scout-agents/{agent_id}/keywords")
def typography_scout_picked_keywords(
    agent_id: str,
    limit: int = Query(default=3, ge=1, le=6),
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = _design_scout_agent(session, agent_id=agent_id, authorization=authorization)
    base = (
        RrugcKeywordVolumeModel.tenant_id == agent.tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    )
    order = (RrugcKeywordVolumeModel.picked_at.desc(), RrugcKeywordVolumeModel.id)
    keywords = session.scalars(
        select(RrugcKeywordVolumeModel).where(*base).order_by(*order).limit(limit)
    ).all()

    # Preserve the latest keyword list for fresh Pinterest searches, while
    # rotating the Stage 0 seed lane past sources already imported on earlier
    # runs. Without this, every one-shot restart downloads the same 3 Pins.
    # The check is tenant-scoped, includes rejected/archived source candidates,
    # and does not require another table, job queue, or a schema migration.
    imported_pin = (
        select(RrugcTypographyReferenceModel.id)
        .where(
            RrugcTypographyReferenceModel.tenant_id == agent.tenant_id,
            RrugcTypographyReferenceModel.source_type == "scout",
            RrugcTypographyReferenceModel.source_url == RrugcKeywordVolumeModel.source_pin_url,
        )
        .correlate(RrugcKeywordVolumeModel)
        .exists()
    )
    source_rows = session.scalars(
        select(RrugcKeywordVolumeModel)
        .where(
            *base,
            RrugcKeywordVolumeModel.source_pin_url.is_not(None),
            RrugcKeywordVolumeModel.source_image_url.is_not(None),
            ~imported_pin,
        )
        .order_by(*order)
        .limit(limit * 20)
    ).all()

    pending_sources = []
    seen_pins: set[str] = set()
    for row in source_rows:
        # Stage 0 may link several picked keywords to one identical Pin.
        # Pick one representative without changing any Stage 0 state.
        try:
            canonical_pin, _ = _canonical_design_pin(row.source_pin_url or "")
        except HTTPException:
            continue
        if canonical_pin in seen_pins or not row.source_image_url:
            continue
        seen_pins.add(canonical_pin)
        pending_sources.append({
            "id": row.id, "keyword": row.keyword,
            "source_pin_url": canonical_pin, "source_image_url": row.source_image_url,
        })
        if len(pending_sources) >= limit:
            break

    return {
        "keywords": [
            {"id": row.id, "keyword": row.keyword,
             "source_pin_url": row.source_pin_url, "source_image_url": row.source_image_url}
            for row in keywords
        ],
        "pending_sources": pending_sources,
    }


@status_router.post("/scout-agents/{agent_id}/references")
async def typography_scout_upload_reference(
    agent_id: str,
    file: UploadFile = File(...),
    source_url: str = Form(..., max_length=512),
    source_query: str = Form(..., max_length=120),
    keyword_id: str = Form(..., max_length=36),
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = _design_scout_agent(session, agent_id=agent_id, authorization=authorization)
    pin_url, pin_id = _canonical_design_pin(source_url)
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == agent.tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    if keyword is None:
        raise HTTPException(status_code=404, detail={"code": "typography_scout_keyword_not_picked"})
    if (file.content_type or "").lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail={"code": "typography_scout_image_format"})
    content = await file.read(DESIGN_SCOUT_BYTES_LIMIT + 1)
    if not content or len(content) > DESIGN_SCOUT_BYTES_LIMIT:
        raise HTTPException(status_code=413, detail={"code": "typography_scout_image_size"})
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = session.scalar(select(func.count(RrugcTypographyReferenceModel.id)).where(
        RrugcTypographyReferenceModel.tenant_id == agent.tenant_id,
        RrugcTypographyReferenceModel.source_type == "scout",
        RrugcTypographyReferenceModel.created_at >= today,
    )) or 0
    if used >= DESIGN_SCOUT_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail={"code": "typography_scout_daily_limit"})

    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(status_code=503, detail={"code": "typography_scout_storage_unavailable"})
    actor = "scout-agent:" + agent.id
    try:
        promoted = await RrugcReferenceLibrary(session).upload_reference(
            tenant_id=agent.tenant_id, user_id=actor,
            original_filename="typography-pin-" + pin_id + ".jpg",
            content=content, storage=storage, reference_type="artwork",
            profile_key=PROFILE,
            allowed_image_formats=frozenset({"PNG", "JPEG", "WEBP"}),
        )
        row, created = TypographyReferenceRepository(session).link_asset(
            tenant_id=agent.tenant_id, workspace_id=agent.tenant_id,
            asset_id=promoted.asset.id, user_id=actor,
            rights="unknown", privacy="workspace", source_type="scout",
            attribution="Pinterest source; rights not verified",
        )
        if created:
            row.source_platform = "pinterest"
            row.source_url = pin_url
            row.scope = "keyword"
            row.keyword_id = keyword.id
            row.tags_json = ["scout", "pinterest", "design-reference"]
            row.notes = "Scout query: " + source_query[:120]
            session.add(AuthAuditEventModel(
                tenant_id=agent.tenant_id, actor_id=actor,
                action="typography_reference.scout_candidate",
                detail_json={"reference_id": row.id, "keyword_id": keyword.id, "source": "pinterest"},
            ))
        session.commit()
        return {"status": "created" if created else "duplicate",
                "reference_id": row.id, "keyword_id": keyword.id,
                "source_rights_status": row.source_rights_status,
                "editorial_status": row.editorial_status}
    except (ReferenceLibraryError, TypographyReferenceError) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail={"code": "typography_scout_conflict"}) from exc


@status_router.get("/status")
def typography_feature_status(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
):
    can_read = principal.platform_admin or "typography_reference.read" in principal.effective_permissions
    return {"available": bool(can_read and _tenant_can_use_v2(principal.active_tenant_id))}


router = APIRouter(
    prefix="/typography",
    tags=["typography-v2"],
    dependencies=[Depends(_gate)],
)


def _error(code: str, message: str, *, status_code: int = 422):
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})



class TypographyPrivateSelectionPreviewRequest(BaseModel):
    """No client-supplied review state, checksums, storage IDs or source text."""
    model_config = ConfigDict(extra="forbid")
    keyword_id: str = Field(min_length=1, max_length=36)
    reference_id: str = Field(min_length=1, max_length=36)
    selected_slots: list[StrictInt] = Field(min_length=1, max_length=6)


@router.post(
    "/batches/{batch_id}/selection-preview",
    dependencies=[
        Depends(require_permission("realistic_review_ugc.read")),
        Depends(require_permission("typography_generate.run")),
    ],
)
def preview_private_typography_selection(
    batch_id: str,
    request: TypographyPrivateSelectionPreviewRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """G6.3 READ-ONLY preview; cannot create Stage1 assets or Stage2 jobs.

    The explicit V2 ImageGen pilot opt-in remains required, even while the
    separate Ideas/Style Lab API-only canary is enabled. A future publisher
    must independently validate all source, PNG and storage receipts.
    """
    tenant = principal.active_tenant_id
    settings = get_settings()
    if not pilot_enabled(settings, tenant):
        raise _error("typography_private_selection_disabled",
                     "Private reviewed selection is not enabled.", status_code=404)

    def current_plan():
        return resolve_pilot_plan(
            session, tenant_id=tenant, user_id=principal.user_id,
            keyword_id=request.keyword_id, reference_id=request.reference_id,
        )

    try:
        result = inspect_reviewed_batch(
            settings=settings, tenant_id=tenant, batch_id=batch_id,
            plan=current_plan(), revalidate=current_plan,
            keyword_id=request.keyword_id, selected_slots=request.selected_slots,
        )
        selected = result["selection_preview"]
    except TypographyPilotError as exc:
        # Intentionally hide tenant, private batch paths, reference rights,
        # and whether another user's batch exists.
        status = 404 if exc.code in {
            "batch_missing", "batch_id_invalid", "reference_not_approved",
            "keyword_not_picked", "dna_not_accepted", "pilot_disabled",
        } else 409
        raise _error(
            "typography_private_selection_unavailable",
            "Reviewed selection is unavailable.", status_code=status,
        ) from exc

    # Explicit response allowlist: the inspector also returns server-only
    # image paths and literal keywords, which must NEVER cross this route.
    return {
        "schema_version": selected["schema_version"],
        "batch_id": batch_id,
        "selection_sha256": selected["selection_sha256"],
        "selected_slots": selected["selected_slots"],
        "selected_count": selected["selected_count"],
        "selected_images": selected["selected_images"],
        "private_only": True,
        "requires_managed_storage_commit": True,
        "published": False,
        "stage1_output_saved": False,
        "stage2_handoff_performed": False,
        "can_queue_stage2": False,
    }




@router.get(
    "/history/{keyword_id}",
    dependencies=[Depends(require_permission("realistic_review_ugc.read"))],
)
def typography_v2_saved_history(
    keyword_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Tenant-scoped metadata only; managed storage IDs never leave the API."""
    from app.modules.realistic_review_ugc.typography_output_history import list_v2_history
    keyword = session.scalar(select(RrugcKeywordVolumeModel.id).where(
        RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
    ))
    if keyword is None:
        raise _error("typography_history_unavailable", "Keyword unavailable.", status_code=404)
    return {"keyword_id": keyword_id, "items": list_v2_history(
        session, tenant_id=principal.active_tenant_id, keyword_id=keyword_id,
    )}


@router.post(
    "/batches/{batch_id}/save-reviewed",
    dependencies=[
        Depends(require_permission("realistic_review_ugc.read")),
        Depends(require_permission("typography_generate.run")),
        Depends(require_permission("realistic_review_ugc.run")),
    ],
)
async def typography_v2_save_approved_batch(
    batch_id: str,
    request: TypographyPrivateSelectionPreviewRequest,
    principal: CurrentPrincipal = Depends(READ),
):
    """Store only approved private PNGs, never queue generation or Stage 2.

    Writer takes fresh short-lived DB sessions and revalidates rights before
    every checkpoint. Each slot has a separately committed audit record.
    """
    from app.core.database import SessionLocal
    from app.modules.realistic_review_ugc.typography_output_history import (
        history_enabled, save_reviewed_v2_outputs,
    )
    settings = get_settings()
    if not history_enabled(settings, principal.active_tenant_id):
        raise _error("typography_history_disabled", "History writer disabled.", status_code=404)
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("typography_history_storage_unavailable", "Managed storage unavailable.", status_code=503)
    try:
        result = await save_reviewed_v2_outputs(
            settings=settings, tenant_id=principal.active_tenant_id,
            user_id=principal.user_id, keyword_id=request.keyword_id,
            reference_id=request.reference_id, batch_id=batch_id,
            selected_slots=request.selected_slots, session_factory=SessionLocal,
            storage=storage,
        )
        return {
            "keyword_id": result["keyword_id"],
            "batch_id": result["batch_id"],
            "saved_count": result["saved_count"],
            "new_count": result["new_count"],
            "items": result["items"],
            "stage2_jobs_created": 0,
            "published": False,
        }
    except TypographyPilotError as exc:
        raise _error("typography_history_unavailable",
                     "Reviewed history save unavailable.", status_code=409) from exc
    except StorageProviderError as exc:
        raise _error("typography_history_storage_unavailable",
                     "Managed storage could not persist reviewed images.",
                     status_code=503) from exc


class TypographyV2HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword_id: str = Field(min_length=1, max_length=36)
    version: StrictInt = Field(ge=1)
    selected: bool


@router.post(
    "/stage2/handoff",
    dependencies=[
        Depends(require_permission("realistic_review_ugc.read")),
        Depends(require_permission("typography_generate.run")),
        Depends(require_permission("realistic_review_ugc.run")),
    ],
)
def select_saved_typography_v2_source(
    request: TypographyV2HandoffRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Explicit manual handoff only; never enqueues Stage 2 or Scout."""
    from app.modules.realistic_review_ugc.typography_stage2_handoff import (
        select_saved_v2_design,
    )
    try:
        result = select_saved_v2_design(
            session, settings=get_settings(),
            tenant_id=principal.active_tenant_id, user_id=principal.user_id,
            keyword_id=request.keyword_id, version=request.version,
            selected=request.selected,
        )
        session.commit()
        return result
    except TypographyPilotError as exc:
        session.rollback()
        raise _error("typography_handoff_unavailable",
                     "Stage 2 handoff unavailable.",
                     status_code=404 if exc.code in {"history_missing", "handoff_disabled", "keyword_not_picked"} else 409) from exc
    except IntegrityError as exc:
        session.rollback()
        raise _error("typography_handoff_conflict",
                     "Stage 2 source has changed.", status_code=409) from exc


class TypographyPreviewRequest(BaseModel):
    """No arbitrary prompt or client-provided DNA: sources are resolved by tenant."""
    model_config = ConfigDict(extra="forbid")
    reference_id: str = Field(min_length=1, max_length=36)
    keyword_id: str = Field(min_length=1, max_length=36)
    count: int = Field(default=3, ge=1, le=3)


@router.post("/previews", dependencies=[Depends(require_permission("realistic_review_ugc.read"))])
def preview_typography_style(
    request: TypographyPreviewRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Read-only G4 preview: never calls ImageGen or creates Stage 1 jobs.

    Requires a currently picked, tenant-scoped keyword and a visible approved
    reference with explicitly accepted, current Style DNA and verified rights.
    """
    tenant = principal.active_tenant_id
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == request.keyword_id,
        RrugcKeywordVolumeModel.tenant_id == tenant,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    if keyword is None:
        raise _error("typography_keyword_not_found", "Picked keyword not found", status_code=404)

    repo = TypographyReferenceRepository(session)
    reference = repo.get(tenant, request.reference_id,
                         user_id=principal.user_id, manage=_can_manage(principal))
    if reference is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    if (reference.editorial_status != "approved"
            or reference.source_rights_status not in VERIFIED_RIGHTS):
        raise _error("typography_reference_not_approved",
                     "Reference must be approved with verified rights", status_code=409)
    dna = repo.dna_cached(
        tenant, reference.id, user_id=principal.user_id,
        analyzer_version=G3_ANALYZER_VERSION,
        image_hash=reference.content_hash, manage=_can_manage(principal),
    )
    effective = effective_reviewed_dna(reference, dna) if dna is not None else None
    if effective is None:
        raise _error("typography_dna_not_accepted",
                     "Accepted, current Style DNA is required", status_code=409)
    try:
        previews = build_typography_previews(
            keyword.keyword, dna=effective, reference_id=reference.id,
            dna_id=dna.id, count=request.count,
        )
        quality = audit_svg_previews(keyword.keyword, previews)
        plan = build_concept_short_v2_plan(
            keyword=keyword.keyword, reviewed_dna=effective,
            reference_id=reference.id, dna_id=dna.id, previews=previews,
        )
    except (TypographyPreviewError, TypographyQualityError) as exc:
        raise _error("typography_preview_invalid", str(exc)) from exc
    # Source SVGs are non-publishable drafts. Never return one with failed
    # technical QA and never imply its spelling was verified after rasterizing.
    if not quality["all_technical_pass"]:
        raise _error("typography_preview_qa_rejected",
                     "Preview failed exact-text or vector-safety validation", status_code=422)
    return {
        "reference_id": reference.id,
        "keyword_id": keyword.id,
        "source_keyword_exact": keyword.keyword,
        "preview_only": True,
        "job_created": False,
        "provider_calls": 0,
        "items": previews,
        "quality": quality,
        "generation_plan": plan,
    }


def _audit(session: Session, principal: CurrentPrincipal, action: str,
           reference_id: str, **details: str | int | bool | None):
    """Atomic tenant audit trail. Never persist images, source URLs or text payloads."""
    session.add(AuthAuditEventModel(
        tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
        action="typography_reference." + action,
        detail_json={"reference_id": reference_id, **details},
    ))


def _can_manage(principal: CurrentPrincipal) -> bool:
    return principal.platform_admin or "typography_reference.manage" in principal.effective_permissions


def _view(row: RrugcTypographyReferenceModel, name: str | None = None) -> dict:
    return {
        "id": row.id,
        "asset_id": row.asset_id,
        "filename": name,
        "content_hash": row.content_hash,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_platform": row.source_platform,
        "source_rights_status": row.source_rights_status,
        "source_attribution": row.source_attribution,
        "privacy": row.privacy,
        "scope": row.scope,
        "campaign_id": row.campaign_id,
        "keyword_id": row.keyword_id,
        "skill_name": row.skill_name,
        "reference_roles": row.reference_roles_json,
        "influence": row.influence,
        "preference": row.preference,
        "priority": row.priority,
        "tags": row.tags_json,
        "notes": row.notes,
        "analysis_status": row.analysis_status,
        "editorial_status": row.editorial_status,
        "created_by_user_id": row.created_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "image_url": f"/api/v1/realistic-review-ugc/typography/references/{row.id}/image",
    }


def _asset_names(session: Session, tenant: str, asset_ids: set[str]) -> dict[str, str | None]:
    if not asset_ids:
        return {}
    rows = session.scalars(
        select(RrugcReferenceAssetModel).where(
            RrugcReferenceAssetModel.tenant_id == tenant,
            RrugcReferenceAssetModel.id.in_(asset_ids),
        )
    )
    return {row.id: row.original_filename for row in rows}


@router.get("/references")
def list_typography_references(
    status: Literal["candidate", "approved", "rejected", "archived"] | None = Query(default=None),
    query: str | None = Query(default=None, max_length=100),
    source_type: Literal["upload", "scout", "internal"] | None = Query(default=None),
    scope: Literal["workspace", "campaign", "keyword", "skill"] | None = Query(default=None),
    style_role: Literal["layout", "font_mood", "overlap"] | None = Query(default=None),
    my_uploads_only: bool = Query(default=False),
    sort: Literal["newest", "oldest", "priority"] = Query(default="newest"),
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repo = TypographyReferenceRepository(session)
    rows, total = repo.list(
        principal.active_tenant_id,
        user_id=principal.user_id,
        manage=_can_manage(principal),
        status=status, query=query, source_type=source_type, scope=scope,
        style_role=style_role, my_uploads_only=my_uploads_only, sort=sort,
        limit=limit, offset=offset,
    )
    names = _asset_names(session, principal.active_tenant_id, {row.asset_id for row in rows})
    rights = principal.effective_permissions
    return {"items": [_view(row, names.get(row.asset_id)) for row in rows],
            "total": total, "limit": limit, "offset": offset,
            "stats": repo.stats(principal.active_tenant_id, user_id=principal.user_id, manage=_can_manage(principal)),
            "capabilities": {
                "read": True,
                "upload": principal.platform_admin or "typography_reference.upload" in rights,
                "manage": _can_manage(principal),
            }}


@router.get("/references/{reference_id}")
def get_typography_reference(
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repo = TypographyReferenceRepository(session)
    row = repo.get(
        principal.active_tenant_id, reference_id,
        user_id=principal.user_id, manage=_can_manage(principal),
    )
    if row is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    names = _asset_names(session, principal.active_tenant_id, {row.asset_id})
    return _view(row, names.get(row.asset_id))


@router.get("/references/{reference_id}/dna")
def list_typography_style_dna(
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repo = TypographyReferenceRepository(session)
    ref = repo.get(principal.active_tenant_id, reference_id, user_id=principal.user_id,
                   manage=_can_manage(principal))
    if ref is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    versions = repo.dna_versions(
        principal.active_tenant_id, reference_id,
        user_id=principal.user_id, manage=_can_manage(principal),
    )
    return {"items": [{
        "id": row.id, "schema_version": row.schema_version,
        "analyzer_version": row.analyzer_version, "style_family": row.style_family,
        "typography_region": row.typography_region_json,
        "font_mood": row.font_mood_json,
        "layout_graph": row.layout_graph_json, "overlap_rules": row.overlap_rules_json,
        "confidence": row.confidence, "analyzed_at": row.analyzed_at,
        "review": review_view(ref, row),
    } for row in versions]}


@router.get("/references/{reference_id}/dna/{dna_id}/similar")
def list_similar_typography_dna(
    reference_id: str,
    dna_id: str,
    max_distance: int = Query(default=6, ge=0, le=8),
    limit: int = Query(default=10, ge=1, le=25),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Advisory-only near-duplicate screening, never automated rejection.

    Bound the candidate scan and filter visibility *inside SQL* so a viewer
    cannot infer the existence of another user's private reference by match
    count or IDs. The dHash is neither returned nor accepted from the client.
    """
    tenant_id = principal.active_tenant_id
    reference = TypographyReferenceRepository(session).get(
        tenant_id, reference_id, user_id=principal.user_id,
        manage=_can_manage(principal),
    )
    if reference is None:
        raise _error("typography_reference_not_found", "Typography reference not found",
                     status_code=404)
    dna = session.scalar(select(RrugcTypographyStyleDnaModel).where(
        RrugcTypographyStyleDnaModel.id == dna_id,
        RrugcTypographyStyleDnaModel.tenant_id == tenant_id,
        RrugcTypographyStyleDnaModel.reference_id == reference_id,
        RrugcTypographyStyleDnaModel.image_hash == reference.content_hash,
        RrugcTypographyStyleDnaModel.analyzer_version == G3_ANALYZER_VERSION,
    ))
    if dna is None:
        raise _error("typography_dna_not_found", "Current Style DNA result not found",
                     status_code=404)
    if not is_typography_dhash(dna.perceptual_hash):
        return {"status": "hash_unavailable", "items": [], "scan_truncated": False}
    # The stored ROI hash refers to the original AI bbox. If a reviewer
    # corrected that bbox, do not claim this old fingerprint represents
    # the effective typography region until a local rescan is performed.
    if has_stale_roi_hash(dna.manual_override_json):
        return {"status": "hash_stale_after_region_edit", "items": [],
                "scan_truncated": False}
    d = RrugcTypographyStyleDnaModel
    r = RrugcTypographyReferenceModel
    candidates = select(d, r).join(r, and_(
        d.tenant_id == r.tenant_id, d.reference_id == r.id,
    )).where(
        d.tenant_id == tenant_id,
        d.reference_id != reference_id,
        d.analyzer_version == G3_ANALYZER_VERSION,
        d.perceptual_hash.like("tdh1:%"),
        r.source_rights_status.in_(VERIFIED_RIGHTS),
        r.editorial_status.in_(("candidate", "approved")),
    )
    if not _can_manage(principal):
        candidates = candidates.where(or_(
            r.privacy == "workspace", r.created_by_user_id == principal.user_id,
        ))
    rows = session.execute(
        candidates.order_by(d.analyzed_at.desc(), d.id.desc()).limit(501),
    ).all()
    matches = []
    for candidate, other_ref in rows[:500]:
        if has_stale_roi_hash(candidate.manual_override_json):
            continue  # Candidate ROI hash also refers to stale AI geometry.
        if not candidate_matches(dna.perceptual_hash, candidate.perceptual_hash,
                                 threshold=max_distance):
            continue
        matches.append({
            "reference_id": other_ref.id, "dna_id": candidate.id,
            "distance": typography_hamming_distance(
                dna.perceptual_hash, candidate.perceptual_hash,
            ),
        })
    matches.sort(key=lambda item: (item["distance"], item["reference_id"]))
    return {
        "status": "ready",
        "items": matches[:limit],
        "scan_truncated": len(rows) > 500,
    }


@router.patch("/references/{reference_id}/dna/{dna_id}/review")
def review_typography_style_dna(
    reference_id: str,
    dna_id: str,
    request: TypographyDnaReviewRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    """Approve, reject or correct generalized DNA, never mutate AI output.

    Feature/tenant/RBAC are checked by router dependencies. Lock both rows
    so a concurrent rights change cannot authorize an out-of-date review.
    All review changes and the audit event commit in a single transaction.
    """
    tenant_id = principal.active_tenant_id
    reference = session.scalar(select(RrugcTypographyReferenceModel).where(
        RrugcTypographyReferenceModel.id == reference_id,
        RrugcTypographyReferenceModel.tenant_id == tenant_id,
    ).with_for_update())
    if reference is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    dna = session.scalar(select(RrugcTypographyStyleDnaModel).where(
        RrugcTypographyStyleDnaModel.id == dna_id,
        RrugcTypographyStyleDnaModel.tenant_id == tenant_id,
        RrugcTypographyStyleDnaModel.reference_id == reference_id,
        RrugcTypographyStyleDnaModel.image_hash == reference.content_hash,
        RrugcTypographyStyleDnaModel.analyzer_version == G3_ANALYZER_VERSION,
    ).with_for_update())
    if dna is None:
        raise _error("typography_dna_not_found", "Current Style DNA result not found",
                     status_code=404)
    if reference.editorial_status == "archived":
        raise _error("typography_reference_inactive", "Archived references cannot be reviewed",
                     status_code=409)
    if request.decision == "accepted" and (
        reference.editorial_status != "approved"
        or reference.source_rights_status not in VERIFIED_RIGHTS
    ):
        raise _error("typography_rights_unverified",
                     "Only active, rights-verified references may be accepted.",
                     status_code=409)
    try:
        review = apply_human_review(dna, request)
    except TypographyReviewError as exc:
        raise _error("typography_dna_review_invalid", str(exc), status_code=422) from exc
    dna.manual_override_json = review
    reference.analysis_status = "ready" if request.decision == "accepted" else "needs_review"
    _audit(
        session, principal, "dna_reviewed", reference_id,
        dna_id=dna.id, analyzer_version=dna.analyzer_version,
        decision=request.decision, reason_code=request.reason_code,
        structural_override=bool(review["override"]),
    )
    session.commit()
    return {"id": dna.id, "reference_id": reference.id, "review": review_view(reference, dna)}


_TYPOGRAPHY_THUMBNAIL_SLOTS = asyncio.Semaphore(3)


@router.get("/references/{reference_id}/image")
async def get_typography_reference_image(
    reference_id: str,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=256, ge=128, le=640),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repo = TypographyReferenceRepository(session)
    ref = repo.get(
        principal.active_tenant_id, reference_id,
        user_id=principal.user_id, manage=_can_manage(principal),
    )
    if ref is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    asset = session.scalar(select(RrugcReferenceAssetModel).where(
        RrugcReferenceAssetModel.tenant_id == principal.active_tenant_id,
        RrugcReferenceAssetModel.id == ref.asset_id,
        RrugcReferenceAssetModel.status == "ready",
    ))
    if asset is None:
        raise _error("typography_asset_unavailable", "Reference image is unavailable.", status_code=404)
    try:
        content_type, _ = reference_image_content_type(asset.image_format)
    except ReferenceLibraryError:
        raise _error("typography_asset_invalid", "Reference image is unsupported.", status_code=422)
    asset_id, remote_id, size_bytes = asset.id, asset.remote_file_id, asset.size_bytes
    revision = asset.content_hash
    # Authorize before conditionals/caching; never hold DB during remote I/O.
    session.close()
    etag = preview_etag(principal.active_tenant_id, remote_id or asset_id, size,
                        revision="typography-webp-v1:" + revision) if thumbnail else None
    cache_headers = {
        "Cache-Control": "private, max-age=300",
        "Vary": "Cookie",
        "X-Content-Type-Options": "nosniff",
    }
    if thumbnail and etag and matches_etag(if_none_match, etag):
        return Response(status_code=304, headers={**cache_headers, "ETag": etag})
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("managed_storage_unavailable", "Managed Storage is unavailable.", status_code=503)
    if thumbnail and (size_bytes is None or 0 < size_bytes <= MAX_REFERENCE_THUMB_BYTES):
        key = (principal.active_tenant_id, "typography-webp-v1", asset_id, f"{revision}:{size}")
        async def load_thumbnail() -> CachedThumbnail:
            async with _TYPOGRAPHY_THUMBNAIL_SLOTS:
                stream = await storage.open_asset(OpenStoredAssetInput(
                    tenant_id=principal.active_tenant_id, asset_id=asset_id,
                    remote_file_id=remote_id, content_type=content_type, size_bytes=size_bytes,
                ))
                try:
                    chunks = bytearray()
                    async for block in stream.body:
                        chunks.extend(block)
                        if len(chunks) > MAX_REFERENCE_THUMB_BYTES:
                            raise ValueError("Reference preview exceeds input limits")
                    webp = await run_in_threadpool(typography_thumbnail_webp, bytes(chunks), size)
                    return CachedThumbnail(content=webp, content_type="image/webp", headers=())
                finally:
                    await stream.close()
        try:
            preview = await thumbnail_cache.get_or_load(key, load_thumbnail)
            return Response(content=preview.content, media_type="image/webp",
                            headers={**cache_headers, "ETag": etag or ""})
        except (StorageProviderError, ValueError, OSError):
            # Use the unmodified original when a compact preview is unavailable.
            pass
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id, asset_id=asset_id,
            remote_file_id=remote_id, content_type=content_type, size_bytes=size_bytes,
        ))
    except StorageProviderError as exc:
        raise _error("typography_asset_unavailable", "Reference image is unavailable.",
                     status_code=503 if exc.retryable else 404) from exc
    return StreamingResponse(
        stream.body, media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers=cache_headers,
    )


@router.post("/references/uploads")
async def upload_typography_references(
    files: list[UploadFile] = File(...),
    rights: Literal["owned", "licensed", "permitted_reference", "unknown", "blocked"] = Form("unknown"),
    privacy: Literal["private", "workspace"] = Form("private"),
    source_attribution: str | None = Form(default=None, max_length=400),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(UPLOAD),
):
    if not (1 <= len(files) <= MAX_BATCH_FILES):
        raise _error("typography_batch_invalid", "Upload 1-12 reference images at a time.")
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("managed_storage_unavailable", "Managed Storage is unavailable.", status_code=503)
    repo = TypographyReferenceRepository(session)
    library = RrugcReferenceLibrary(session)
    results: list[dict] = []
    for index, file in enumerate(files):
        filename = (file.filename or f"reference-{index + 1}")[:180]
        if (file.content_type or "").lower() not in ALLOWED_CONTENT_TYPES:
            results.append({"index": index, "filename": filename, "status": "error",
                            "code": "typography_format_unsupported"})
            continue
        content = await file.read(REFERENCE_ASSET_MAX_BYTES + 1)
        if not content or len(content) > REFERENCE_ASSET_MAX_BYTES:
            results.append({"index": index, "filename": filename, "status": "error",
                            "code": "typography_empty_or_too_large"})
            continue
        try:
            promoted = await library.upload_reference(
                tenant_id=principal.active_tenant_id,
                user_id=principal.user_id,
                original_filename=filename,
                content=content,
                storage=storage,
                reference_type="artwork",
                profile_key=PROFILE,
                allowed_image_formats=frozenset({"PNG", "JPEG", "WEBP"}),
            )
            row, created = repo.link_asset(
                tenant_id=principal.active_tenant_id,
                workspace_id=principal.active_tenant_id,
                asset_id=promoted.asset.id,
                user_id=principal.user_id,
                rights=rights,
                privacy=privacy,
                attribution=source_attribution,
            )
            if created:
                _audit(session, principal, "upload", row.id, asset_id=row.asset_id,
                       privacy=row.privacy, source_rights_status=row.source_rights_status)
            session.commit()
            session.refresh(row)
            results.append({"index": index, "filename": filename,
                            "status": "created" if created else "duplicate",
                            "reference": _view(row, promoted.asset.original_filename),
                            "stored_new_asset": promoted.created})
        except (ReferenceLibraryError, TypographyReferenceError) as exc:
            session.rollback()
            results.append({"index": index, "filename": filename, "status": "error",
                            "code": exc.code})
        except IntegrityError:
            # A concurrent dedup write may have won the unique tenant+hash race.
            # Never return someone else's private reference from that race.
            session.rollback()
            results.append({"index": index, "filename": filename, "status": "error",
                            "code": "typography_reference_conflict"})
    return {"items": results, "total": len(results),
            "created": sum(item["status"] == "created" for item in results),
            "duplicates": sum(item["status"] == "duplicate" for item in results),
            "failed": sum(item["status"] == "error" for item in results)}


def _analysis_job_view(
    session: Session, principal: CurrentPrincipal, reference_id: str,
) -> dict:
    """Never expose lease tokens, provider credentials, private storage IDs."""
    tenant_id = principal.active_tenant_id
    ref = TypographyReferenceRepository(session).get(
        tenant_id, reference_id,
        user_id=principal.user_id, manage=_can_manage(principal),
    )
    if ref is None:
        raise _error("typography_reference_not_found", "Typography reference not found",
                     status_code=404)
    job = session.scalar(select(RrugcTypographyAnalysisJobModel).where(
        RrugcTypographyAnalysisJobModel.tenant_id == tenant_id,
        RrugcTypographyAnalysisJobModel.reference_id == reference_id,
        RrugcTypographyAnalysisJobModel.analyzer_version == G3_ANALYZER_VERSION,
        RrugcTypographyAnalysisJobModel.image_hash == ref.content_hash,
    ))
    dna = session.scalar(select(RrugcTypographyStyleDnaModel).where(
        RrugcTypographyStyleDnaModel.tenant_id == tenant_id,
        RrugcTypographyStyleDnaModel.reference_id == reference_id,
        RrugcTypographyStyleDnaModel.analyzer_version == G3_ANALYZER_VERSION,
        RrugcTypographyStyleDnaModel.image_hash == ref.content_hash,
    ))
    rights_ok = ref.source_rights_status in VERIFIED_RIGHTS
    active = ref.editorial_status not in {"archived", "rejected"}
    worker_enabled = _g3_worker_enabled(tenant_id)
    return {
        "reference_id": reference_id,
        "analyzer_version": G3_ANALYZER_VERSION,
        "analysis_status": ref.analysis_status,
        "dna_available": dna is not None,
        "dna_review_status": review_view(ref, dna)["decision"] if dna else "pending",
        "worker_enabled": worker_enabled,
        "can_request_analysis": bool(_can_manage(principal) and worker_enabled and rights_ok and active),
        "analysis_blocker": (
            "reference_inactive" if not active else
            "rights_unverified" if not rights_ok else
            "worker_disabled" if not worker_enabled else None
        ),
        "job": ({
            "id": job.id,
            "status": job.status,
            "attempt_count": job.attempt_count,
            "next_attempt_at": job.next_attempt_at,
            "last_error_code": job.last_error_code,
        } if job is not None else None),
    }


@router.get("/references/{reference_id}/analysis")
def get_typography_analysis_status(
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    return _analysis_job_view(session, principal, reference_id)


@router.post("/references/{reference_id}/analyze")
def enqueue_typography_analysis(
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    """Explicit, idempotent request. This does NOT run any AI model or worker."""
    tenant_id = principal.active_tenant_id
    if not _tenant_can_use_v2(tenant_id):
        raise _error("typography_v2_disabled", "Typography V2 is not enabled.",
                     status_code=404)
    queue = TypographyAnalysisQueue(session)
    job, outcome = queue.enqueue(
        tenant_id=tenant_id,
        reference_id=reference_id,
        user_id=principal.user_id,
        analyzer_version=G3_ANALYZER_VERSION,
        enabled=True, tenant_allowed=True, manage=True,
    )
    if outcome == "reference_unavailable":
        raise _error("typography_reference_not_found", "Typography reference not found",
                     status_code=404)
    if outcome not in {"created", "existing", "cache_hit"}:
        raise _error("typography_analysis_unavailable", "Analysis requires an active, rights-verified reference.",
                     status_code=409)
    if outcome == "created":
        _audit(session, principal, "analysis_requested", reference_id,
               analyzer_version=G3_ANALYZER_VERSION, job_id=job.id)
    session.commit()
    response = _analysis_job_view(session, principal, reference_id)
    return {"outcome": outcome, **response}


# Explicit one-shot UI dispatch. Never auto-analyze an uploaded/scouted image
# or bypass the existing rights/tenant/rollback/permission boundaries.
_G3_UI_SLOTS = asyncio.Semaphore(2)


async def _g3_one_shot_background(tenant_id: str) -> None:
    if not _g3_worker_enabled(tenant_id):
        return
    from app.core.database import SessionLocal
    from app.providers.ai.factory import build_ai_provider_registry
    from app.domain.providers.registry import AiProviderUnavailableError
    from app.modules.realistic_review_ugc.typography_visual_analyzer import (
        TypographyVisualAnalysisRunner,
    )
    settings = get_settings()
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        return
    async with _G3_UI_SLOTS:
        registry = build_ai_provider_registry(settings, session_factory=SessionLocal)
        try:
            try:
                provider = registry.require("gemini")
            except AiProviderUnavailableError:
                return
            runner = TypographyVisualAnalysisRunner(
                session_factory=SessionLocal, storage=storage, provider=provider,
            )
            await runner.tick(tenant_id=tenant_id,
                              worker_id="g3-manual-" + uuid4().hex[:12], concurrency=1)
        finally:
            await registry.aclose()


@router.post("/references/{reference_id}/analyze-and-run", status_code=202)
async def request_and_run_typography_analysis(
    reference_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    """An explicit user click queues + dispatches at most one guarded G3 job.

    No hidden bulk jobs, scheduled scans or automatic billing. BackgroundTasks
    returns promptly while G3 uses the durable tenant/lease-fenced queue.
    """
    tenant = principal.active_tenant_id
    if not _g3_worker_enabled(tenant):
        raise _error("typography_analyzer_disabled",
                     "The G3 analyzer is not enabled for this tenant.", status_code=409)
    reference = TypographyReferenceRepository(session).get(
        tenant, reference_id, user_id=principal.user_id, manage=True,
    )
    if reference is None:
        raise _error("typography_reference_not_found", "Reference not found.", status_code=404)
    if (reference.source_rights_status not in VERIFIED_RIGHTS
            or reference.editorial_status in {"archived", "rejected"}):
        raise _error("typography_analysis_unavailable",
                     "Verify source rights and keep this reference active before analysis.",
                     status_code=409)
    settings = get_settings()
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("typography_storage_unavailable",
                     "Managed Storage is not configured.", status_code=503)
    from app.core.database import SessionLocal
    from app.providers.ai.factory import build_ai_provider_registry
    from app.domain.providers.registry import AiProviderUnavailableError
    registry = build_ai_provider_registry(settings, session_factory=SessionLocal)
    try:
        try:
            registry.require("gemini")
        except AiProviderUnavailableError as exc:
            raise _error("typography_analyzer_provider_unavailable",
                         "No G3 vision provider is configured.", status_code=503) from exc
    finally:
        await registry.aclose()
    job, outcome = TypographyAnalysisQueue(session).enqueue(
        tenant_id=tenant, reference_id=reference_id, user_id=principal.user_id,
        analyzer_version=G3_ANALYZER_VERSION,
        enabled=True, tenant_allowed=True, manage=True,
    )
    if outcome == "reference_unavailable":
        raise _error("typography_reference_not_found",
                     "Typography reference not found", status_code=404)
    if outcome not in {"created", "existing", "cache_hit"}:
        raise _error("typography_analysis_unavailable",
                     "Verify source rights and keep the reference active before analysis.",
                     status_code=409)
    if outcome == "created":
        _audit(session, principal, "analysis_requested", reference_id,
               analyzer_version=G3_ANALYZER_VERSION, job_id=job.id)
    session.commit()
    response = {"outcome": outcome, **_analysis_job_view(session, principal, reference_id)}
    if outcome != "cache_hit" and job is not None and job.status == "queued":
        background_tasks.add_task(_g3_one_shot_background, tenant)
    return response


class TypographyReferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    editorial_status: Literal["candidate", "approved", "rejected", "archived"] | None = None
    source_rights_status: Literal["owned", "licensed", "permitted_reference", "unknown", "blocked"] | None = None
    privacy: Literal["private", "workspace"] | None = None
    scope: Literal["workspace", "campaign", "keyword", "skill"] | None = None
    campaign_id: str | None = Field(default=None, max_length=36)
    keyword_id: str | None = Field(default=None, max_length=36)
    skill_name: str | None = Field(default=None, max_length=128)
    preference: Literal["positive", "negative", "neutral"] | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)
    tags: list[str] | None = Field(default=None, max_length=32)
    reference_roles: list[Literal["layout", "font_mood", "overlap"]] | None = Field(default=None, max_length=3)
    source_attribution: str | None = Field(default=None, max_length=400)
    notes: str | None = Field(default=None, max_length=2000)


@router.patch("/references/{reference_id}")
def patch_typography_reference(
    reference_id: str,
    update: TypographyReferencePatch,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    repo = TypographyReferenceRepository(session)
    row = repo.get(principal.active_tenant_id, reference_id,
                   user_id=principal.user_id, manage=True)
    if row is None:
        raise _error("typography_reference_not_found", "Typography reference not found", status_code=404)
    # Scope is metadata, but IDs must be active-tenant records. A caller can
    # neither point to another tenant's campaign/keyword nor silently inherit
    # stale identifiers when switching the scope type.
    scoped_fields = {"scope", "campaign_id", "keyword_id", "skill_name"}
    if scoped_fields & update.model_fields_set:
        changed_scope = update.scope is not None and update.scope != row.scope
        target_scope = update.scope or row.scope
        values = {
            key: (
                getattr(update, key) if key in update.model_fields_set
                else None if changed_scope else getattr(row, key)
            ) for key in ("campaign_id", "keyword_id", "skill_name")
        }
        campaign, keyword, skill = (values[key] for key in ("campaign_id", "keyword_id", "skill_name"))
        if (target_scope == "workspace" and (campaign or keyword or skill)
                or target_scope == "campaign" and (not campaign or keyword or skill)
                or target_scope == "keyword" and (not keyword or skill)
                or target_scope == "skill" and (not skill or campaign or keyword)):
            raise _error("typography_scope_invalid", "Scope requires a matching campaign, keyword, or skill.")
        if campaign and session.scalar(select(RrugcCampaignModel.id).where(
                RrugcCampaignModel.tenant_id == principal.active_tenant_id,
                RrugcCampaignModel.id == campaign,
        )) is None:
            raise _error("typography_scope_not_found", "Campaign not found.", status_code=404)
        if keyword and session.scalar(select(RrugcKeywordVolumeModel.id).where(
                RrugcKeywordVolumeModel.tenant_id == principal.active_tenant_id,
                RrugcKeywordVolumeModel.id == keyword,
        )) is None:
            raise _error("typography_scope_not_found", "Keyword not found.", status_code=404)
        row.scope = target_scope
        row.campaign_id, row.keyword_id, row.skill_name = campaign, keyword, skill
    if ("editorial_status" in update.model_fields_set
            or "source_rights_status" in update.model_fields_set):
        try:
            desired_status = update.editorial_status or row.editorial_status
            # A rights revocation must always be possible for an approved
            # reference. If editorial_status was not explicitly supplied,
            # revoke the approval rather than retain an approved/blocked row.
            # Explicit attempts to approve unknown/blocked rights still fail.
            if ("editorial_status" not in update.model_fields_set
                    and update.source_rights_status is not None
                    and update.source_rights_status not in VERIFIED_RIGHTS
                    and desired_status == "approved"):
                desired_status = "candidate"
            row = repo.update_editorial(
                principal.active_tenant_id, reference_id, user_id=principal.user_id,
                status=desired_status,
                rights=update.source_rights_status,
            )
        except TypographyReferenceError as exc:
            raise _error(exc.code, str(exc)) from exc
        if row.editorial_status == "archived":
            row.archived_at = datetime.now(timezone.utc)
        else:
            row.archived_at = None
    if update.privacy is not None:
        row.privacy = update.privacy
    if update.preference is not None:
        row.preference = update.preference
    if update.priority is not None:
        row.priority = update.priority
    if "tags" in update.model_fields_set:
        row.tags_json = list(dict.fromkeys(tag.strip()[:80] for tag in (update.tags or [])
                                               if tag.strip()))[:32]
    if "reference_roles" in update.model_fields_set:
        roles = list(dict.fromkeys(update.reference_roles or []))
        if not roles:
            raise _error("typography_roles_required", "At least one style component is required.")
        row.reference_roles_json = roles
    if "notes" in update.model_fields_set:
        row.notes = update.notes
    if "source_attribution" in update.model_fields_set:
        row.source_attribution = update.source_attribution
    _audit(session, principal, "update", row.id,
           changed_fields=",".join(sorted(update.model_fields_set)),
           editorial_status=row.editorial_status, privacy=row.privacy)
    session.commit()
    session.refresh(row)
    names = _asset_names(session, principal.active_tenant_id, {row.asset_id})
    return _view(row, names.get(row.asset_id))


@router.delete("/references/{reference_id}")
def archive_typography_reference(
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(MANAGE),
):
    repo = TypographyReferenceRepository(session)
    try:
        row = repo.update_editorial(
            principal.active_tenant_id, reference_id, user_id=principal.user_id,
            status="archived",
        )
    except TypographyReferenceError as exc:
        raise _error(exc.code, str(exc), status_code=404 if exc.code == "reference_not_found" else 422) from exc
    row.archived_at = datetime.now(timezone.utc)
    _audit(session, principal, "archive", row.id, editorial_status="archived")
    session.commit()
    return {"id": row.id, "editorial_status": "archived"}