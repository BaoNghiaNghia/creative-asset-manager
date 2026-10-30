"""Tenant-authorized Creative Brief API, separate from image generation endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.creative_brief_service import (
    get_brief, get_v3_preview, queue_brief, queue_picked_backfill, v3_enabled,
)

router = APIRouter(prefix="/keyword-creative-briefs", tags=["creative-brief"])
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")


@router.get("/v3/status")
def creative_engine_v3_status(principal: CurrentPrincipal = Depends(READ)):
    """Capability probe: HTTP 200 when OFF, no tenant identifiers or data."""
    return {"available": v3_enabled(get_settings(), principal.active_tenant_id),
            "schema_version": "creative-v3.0-preview-v1"}


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
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    if not get_settings().RRUGC_CREATIVE_BRIEF_ENABLED:
        raise HTTPException(status_code=503, detail="Creative Brief is disabled.")
    result = queue_picked_backfill(
        session, tenant_id=principal.active_tenant_id,
        limit=limit, offset=offset, settings=get_settings(),
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
        )
        session.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Picked keyword not found") from exc
    return {"keyword_id": keyword_id, "status": status, "queued": queued}
