"""G1.2 Typography Ideas Library API, off by default and strictly tenant-scoped.

No generation, AI analysis, Scout lease, or Stage 1 v1 behavior is exposed here.
Physical bytes continue to use the existing managed Reference Library.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import get_db
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
from app.modules.realistic_review_ugc.typography_dna_review import (
    TypographyDnaReviewRequest, TypographyReviewError, apply_human_review,
    review_view, VERIFIED_RIGHTS,
)
from app.modules.realistic_review_ugc.typography_similarity import (
    is_typography_dhash, candidate_matches, typography_hamming_distance,
    has_stale_roi_hash,
)
from app.modules.realistic_review_ugc.typography_repository import (
    TypographyReferenceError, TypographyReferenceRepository,
)
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


@router.get("/references/{reference_id}/image")
async def get_typography_reference_image(
    reference_id: str,
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
    asset_id, remote_id, size = asset.id, asset.remote_file_id, asset.size_bytes
    # Never hold a DB connection while Managed Storage streams the image.
    session.close()
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("managed_storage_unavailable", "Managed Storage is unavailable.", status_code=503)
    try:
        stream = await storage.open_asset(OpenStoredAssetInput(
            tenant_id=principal.active_tenant_id, asset_id=asset_id,
            remote_file_id=remote_id, content_type=content_type, size_bytes=size,
        ))
    except StorageProviderError as exc:
        raise _error("typography_asset_unavailable", "Reference image is unavailable.",
                     status_code=503 if exc.retryable else 404) from exc
    return StreamingResponse(
        stream.body, media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
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
    return {
        "reference_id": reference_id,
        "analyzer_version": G3_ANALYZER_VERSION,
        "analysis_status": ref.analysis_status,
        "dna_available": dna is not None,
        "dna_review_status": review_view(ref, dna)["decision"] if dna else "pending",
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
            row = repo.update_editorial(
                principal.active_tenant_id, reference_id, user_id=principal.user_id,
                status=update.editorial_status or row.editorial_status,
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
