"""Read and stage metadata verification tasks; no scan execution endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.scout_session_router import online
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_service import _task_dict, stage_from_snapshot

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-scan-tasks"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")


class TaskStage(BaseModel):
    model_config = {"extra": "forbid"}
    snapshot_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


@router.post("/shops/{shop_id}/scan-tasks", status_code=201)
def stage_task(
    shop_id: str, body: TaskStage, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    task, replayed = stage_from_snapshot(
        session, tenant_id=principal.active_tenant_id, shop_id=shop_id,
        snapshot_id=body.snapshot_id, actor_id=principal.actor_id,
    )
    return {"item": _task_dict(task), "replayed": replayed,
            "scan_enabled": False, "dispatchable": False}


@router.get("/shops/{shop_id}/scan-tasks")
def list_scan_tasks(
    shop_id: str, limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    criteria = (BrandScanTaskModel.tenant_id == tenant, BrandScanTaskModel.shop_id == shop_id)
    count = session.scalar(select(func.count()).select_from(BrandScanTaskModel).where(*criteria)) or 0
    tasks = session.scalars(
        select(BrandScanTaskModel).where(*criteria)
        .order_by(BrandScanTaskModel.created_at.desc(), BrandScanTaskModel.id.desc())
        .offset(offset).limit(limit)
    ).all()
    return {
        "items": [_task_dict(row) for row in tasks],
        "count": count, "limit": limit, "offset": offset,
        "scan_enabled": False, "dispatchable": False,
    }


@router.post("/shops/{shop_id}/scan-tasks/{task_id}/cancel")
def cancel_staged_task(
    shop_id: str, task_id: str, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    task = session.scalar(select(BrandScanTaskModel).where(
        BrandScanTaskModel.id == task_id,
        BrandScanTaskModel.shop_id == shop_id,
        BrandScanTaskModel.tenant_id == principal.active_tenant_id,
    ).with_for_update())
    if task is None:
        raise HTTPException(404, detail="Scan task not found")
    if task.status != "staged":
        raise HTTPException(409, detail="Only non-dispatchable staged tasks can be cancelled")
    task.status = "cancelled"
    session.commit()
    return {"item": _task_dict(task), "scan_enabled": False}


@router.get("/shops/{shop_id}/scan-readiness")
def scan_readiness(
    shop_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    """Tenant-bound operator view of existing evidence and staged work.

    A live heartbeat is connectivity only, never proof of platform permission.
    This endpoint cannot transition tasks or deliver browser commands.
    """
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")

    status_counts = dict(session.execute(
        select(BrandScanTaskModel.status, func.count())
        .where(BrandScanTaskModel.tenant_id == tenant,
               BrandScanTaskModel.shop_id == shop_id)
        .group_by(BrandScanTaskModel.status)
    ).all())
    states = ("staged", "pending", "leased", "retry", "blocked", "completed", "cancelled")
    task_counts = {state: int(status_counts.get(state, 0)) for state in states}
    snapshot_counts = dict(session.execute(
        select(BrandListingSnapshotModel.evidence_state, func.count())
        .where(BrandListingSnapshotModel.tenant_id == tenant,
               BrandListingSnapshotModel.shop_id == shop_id)
        .group_by(BrandListingSnapshotModel.evidence_state)
    ).all())
    sessions = session.execute(
        select(BrandScoutSessionModel, RrugcScoutAgentModel.active)
        .join(RrugcScoutAgentModel,
              (BrandScoutSessionModel.agent_id == RrugcScoutAgentModel.id) &
              (BrandScoutSessionModel.tenant_id == RrugcScoutAgentModel.tenant_id))
        .where(BrandScoutSessionModel.tenant_id == tenant)
        .limit(100)
    ).all()
    clock = now_utc()
    connected = sum(bool(active and online(item.last_seen_at, clock)) for item, active in sessions)
    blockers = ["scan_task_dispatch_not_deployed", "marketplace_authorization_not_verified"]
    if shop.platform not in ("amazon", "etsy"):
        blockers.append("platform_not_supported_by_browser_pilot")
    if shop.access_mode not in ("owned", "authorized"):
        blockers.append("shop_access_not_declared")
    if connected == 0:
        blockers.append("paired_agent_not_online")
    if shop.platform == "etsy":
        blockers.append("etsy_written_permission_not_verified")
    if shop.platform == "amazon":
        blockers.append("amazon_agent_policy_review_not_verified")

    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id, "platform": shop.platform,
        "access_mode": shop.access_mode,
        "paired_agents_online": connected,
        "paired_agents_seen": len(sessions),
        "listing_snapshot_count": sum(snapshot_counts.values()),
        "verified_listing_count": snapshot_counts.get("verified", 0),
        "task_counts": task_counts,
        "scan_enabled": False, "dispatchable": False,
        "active_scan_jobs": 0, "marketplace_authorization_verified": False,
        "blockers": blockers,
        "note": "Staged tasks and heartbeats do not authorize marketplace scans or verify imported evidence.",
    }
