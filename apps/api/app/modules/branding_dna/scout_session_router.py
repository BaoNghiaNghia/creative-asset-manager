"""Authenticated paired Scout standby presence. Does not dispatch browser jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
from app.modules.realistic_review_ugc.service import RrugcError

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-paired-presence"])
READ = require_permission("ai_operations.read")
PRESENCE_TTL = timedelta(seconds=180)


class ScoutStandbyHeartbeat(BaseModel):
    model_config = {"extra": "forbid"}
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    state: Literal["connected_waiting"]
    scan_enabled: Literal[False]
    active_scan_jobs: Literal[0]


def paired_scout(session: Session, agent_id: str, authorization: str | None):
    parts = (authorization or "").split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(401, detail="Paired Scout token required")
    try:
        return RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=parts[1].strip())
    except RrugcError:
        raise HTTPException(401, detail="Invalid paired Scout credentials") from None


def online(last_seen_at: datetime, now: datetime) -> bool:
    last = last_seen_at.replace(tzinfo=timezone.utc) if last_seen_at.tzinfo is None else last_seen_at.astimezone(timezone.utc)
    delta = now - last
    return timedelta(0) <= delta <= PRESENCE_TTL


@router.post("/scout-agents/{agent_id}/branding-heartbeat")
def standby_heartbeat(
    agent_id: str, body: ScoutStandbyHeartbeat, response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    scout = paired_scout(session, agent_id, authorization)
    clock = now_utc()
    shop_count = session.scalar(select(func.count()).select_from(BrandShopModel).where(
        BrandShopModel.tenant_id == scout.tenant_id,
        BrandShopModel.platform.in_(("amazon", "etsy")),
    )) or 0
    record = session.get(BrandScoutSessionModel, agent_id)
    if record is not None and record.tenant_id != scout.tenant_id:
        raise HTTPException(403, detail="Paired Scout belongs to another tenant")
    if record is None:
        record = BrandScoutSessionModel(agent_id=agent_id, tenant_id=scout.tenant_id,
                                        version=body.version, shop_count=min(shop_count, 200),
                                        last_seen_at=clock)
        session.add(record)
    else:
        record.version = body.version
        record.shop_count = min(shop_count, 200)
        record.last_seen_at = clock
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True, "state": "connected_waiting",
            "scan_enabled": False, "active_scan_jobs": 0,
            "shop_count": min(shop_count, 200), "next_heartbeat_seconds": 60}


@router.get("/scout-agents/presence")
def list_paired_presence(
    response: Response, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
    clock = now_utc()
    rows = session.execute(select(BrandScoutSessionModel, RrugcScoutAgentModel.active).join(
        RrugcScoutAgentModel,
        (BrandScoutSessionModel.agent_id == RrugcScoutAgentModel.id)
        & (BrandScoutSessionModel.tenant_id == RrugcScoutAgentModel.tenant_id),
    ).where(BrandScoutSessionModel.tenant_id == principal.active_tenant_id)
       .order_by(BrandScoutSessionModel.last_seen_at.desc()).limit(100)).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [
        {"agent_id": record.agent_id, "version": record.version,
         "shop_count": record.shop_count, "last_seen_at": record.last_seen_at.isoformat(),
         "online": bool(active and online(record.last_seen_at, clock)),
         "state": "connected_waiting" if active and online(record.last_seen_at, clock) else "offline"}
        for record, active in rows
    ], "scan_enabled": False, "dispatch_state": "disabled"}

@router.get("/scout-agents/{agent_id}/dispatch-preview")
def dispatch_preview(
    agent_id: str, response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Tenant-scoped, read-only readiness view for a paired Scout.

    This is NOT a lease, approval, signed job, or execution command. It must
    never return instructions to browse a shop or advance staged tasks.
    """
    scout = paired_scout(session, agent_id, authorization)
    shops = session.scalars(
        select(BrandShopModel)
        .where(BrandShopModel.tenant_id == scout.tenant_id,
               BrandShopModel.platform.in_(("amazon", "etsy")))
        .order_by(BrandShopModel.created_at.asc(), BrandShopModel.id.asc())
        .limit(200)
    ).all()
    counts = dict(session.execute(
        select(BrandScanTaskModel.shop_id, func.count())
        .where(BrandScanTaskModel.tenant_id == scout.tenant_id,
               BrandScanTaskModel.status == "staged")
        .group_by(BrandScanTaskModel.shop_id)
    ).all())
    items = []
    for shop in shops:
        blockers = [
            "scan_task_dispatch_not_deployed",
            "marketplace_authorization_not_verified",
        ]
        if shop.access_mode not in ("owned", "authorized"):
            blockers.append("shop_access_not_declared")
        if shop.status != "active":
            blockers.append("shop_not_active")
        if int(counts.get(shop.id, 0)) == 0:
            blockers.append("no_staged_scan_tasks")
        if shop.platform == "etsy":
            blockers.append("etsy_written_permission_not_verified")
        else:
            blockers.append("amazon_agent_policy_review_not_verified")
        items.append({
            "shop_id": shop.id,
            "platform": shop.platform,
            "display_name": shop.display_name,
            "status": "blocked",
            "staged_tasks": int(counts.get(shop.id, 0)),
            "blockers": blockers,
        })
    response.headers["Cache-Control"] = "no-store"
    return {
        "version": 1,
        "scan_enabled": False,
        "dispatch": {
            "state": "disabled",
            "reason": "scan_task_dispatch_not_deployed",
            "active_scan_jobs": 0,
            "marketplace_authorization_verified": False,
        },
        "shop_count": len(items),
        "blocked_count": len(items),
        "ready_count": 0,
        "items": items,
    }
