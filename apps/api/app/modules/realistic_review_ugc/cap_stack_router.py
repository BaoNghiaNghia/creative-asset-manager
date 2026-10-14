"""Tenant-isolated shared Cap Stack library and separately leased Scout lane.

This intentionally never creates campaigns/source plans or writes Stage 3
candidate feedback. The auto label/count vision classification is NOT inferred
from a Pinterest title: unreviewed images remain pending until verified.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import random
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackControlModel, RrugcCapStackImageModel
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
from app.modules.realistic_review_ugc.service import RrugcError

router = APIRouter(prefix="/cap-stack", tags=["realistic-review-ugc-cap-stack"])
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")
LANE_TARGET = 12
LEASE_MINUTES = 45
SCOUT_SEARCHES = ("Cap Stack", "baseball cap stack", "stacked trucker hats", "pile of baseball caps")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def pin_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or host not in {"www.pinterest.com", "pinterest.com"}:
        raise HTTPException(422, "Pinterest HTTPS pin link required.")
    match = re.fullmatch(r"/pin/(\d+)/?", parts.path)
    if not match:
        raise HTTPException(422, "Pinterest pin numeric ID required.")
    return match.group(1)


def validate_image_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or not (host == "pinimg.com" or host.endswith(".pinimg.com")):
        raise HTTPException(422, "Pinterest pinimg HTTPS image required.")
    if parts.username or parts.password or parts.port or not parts.path:
        raise HTTPException(422, "Unexpected Pinterest image URL.")


def get_control(session: Session, tenant: str, *, lock: bool = False):
    stmt = select(RrugcCapStackControlModel).where(RrugcCapStackControlModel.tenant_id == tenant)
    return session.scalar(stmt.with_for_update() if lock else stmt)


def agent_auth(session: Session, agent_id: str, authorization: str | None):
    parts = str(authorization or "").split(" ", 1)
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1].strip():
        raise HTTPException(401, "Scout token required.")
    try:
        return RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=parts[1].strip(),
        )
    except RrugcError:
        raise HTTPException(401, "Invalid Scout credentials.") from None


def image_dict(row: RrugcCapStackImageModel) -> dict:
    return {
        "id": row.id, "pin_url": row.pin_url, "image_url": row.image_url,
        "alt_text": row.alt_text, "source_query": row.source_query,
        "caps_detected": row.caps_detected, "count_status": row.count_status,
        "count_confidence": row.count_confidence,
        "manual_caps_count": row.manual_caps_count, "manual_label": row.manual_label,
        "created_at": row.created_at,
    }


class SettingsRequest(BaseModel):
    enabled: bool


class FeedbackRequest(BaseModel):
    label: str | None = Field(default=None, pattern="^(good|bad|ai|clear)$")
    caps_count: int | None = Field(default=None, ge=1, le=50)


class ScoutCandidate(BaseModel):
    pin_url: str = Field(min_length=18, max_length=2048)
    image_url: str = Field(min_length=15, max_length=4096)
    alt_text: str | None = Field(default=None, max_length=2000)


class ScoutBatch(BaseModel):
    items: list[ScoutCandidate] = Field(min_length=1, max_length=24)
    source_query: str | None = Field(default=None, max_length=256)


class CompleteRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed|needs_login)$")
    error_code: str | None = Field(default=None, max_length=100)


class HeartbeatRequest(BaseModel):
    status: str = Field(default="busy")


@router.get("")
def list_cap_stack(
    q: str = Query(default="", max_length=160),
    label: str = Query(default="all", pattern="^(all|pending|good|bad|ai)$"),
    caps_count: int | None = Query(default=None, ge=1, le=50),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=40, ge=1, le=100),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    stmt = select(RrugcCapStackImageModel).where(RrugcCapStackImageModel.tenant_id == tenant)
    if q.strip():
        escaped = q.strip().replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            RrugcCapStackImageModel.alt_text.ilike("%" + escaped + "%", escape="\\")
            | RrugcCapStackImageModel.source_query.ilike("%" + escaped + "%", escape="\\")
        )
    if label == "pending":
        stmt = stmt.where(RrugcCapStackImageModel.manual_label.is_(None))
    elif label != "all":
        stmt = stmt.where(RrugcCapStackImageModel.manual_label == label)
    if caps_count is not None:
        stmt = stmt.where(
            func.coalesce(RrugcCapStackImageModel.manual_caps_count,
                          RrugcCapStackImageModel.caps_detected) == caps_count
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(session.scalars(
        stmt.order_by(RrugcCapStackImageModel.created_at.desc(), RrugcCapStackImageModel.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    totals = Counter(
        row or "pending" for row in session.scalars(
            select(RrugcCapStackImageModel.manual_label).where(
                RrugcCapStackImageModel.tenant_id == tenant
            )
        )
    )
    control = get_control(session, tenant)
    return {
        "items": [image_dict(row) for row in rows], "total": total,
        "page": page, "page_size": page_size,
        "stats": {"discovered": sum(totals.values()), "approved": totals["good"],
                  "rejected": totals["bad"], "ai": totals["ai"], "pending": totals["pending"]},
        "scout": {
            "enabled": bool(control.enabled) if control else False,
            "running": bool(control and control.lease_run_id
                            and utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > utc_now()),
            "last_result": control.last_result if control else None,
            "next_scan_at": control.next_scan_at if control else None,
        },
    }


@router.post("/settings")
def set_cap_stack_settings(
    body: SettingsRequest, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    tenant = principal.active_tenant_id
    control = get_control(session, tenant, lock=True)
    if control is None:
        control = RrugcCapStackControlModel(tenant_id=tenant, enabled=body.enabled)
        session.add(control)
    else:
        control.enabled = body.enabled
    if not body.enabled:
        control.lease_run_id = None
        control.lease_agent_id = None
        control.lease_expires_at = None
    else:
        control.next_scan_at = None
    session.commit()
    return {"enabled": control.enabled}


@router.post("/{image_id}/feedback")
def set_cap_stack_feedback(
    image_id: str, body: FeedbackRequest, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row = session.scalar(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == principal.active_tenant_id,
        RrugcCapStackImageModel.id == image_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Cap Stack image not found.")
    if body.label is not None:
        row.manual_label = None if body.label == "clear" else body.label
        row.reviewed_at = utc_now()
        row.feedback_by = str(getattr(principal, "user_id", "") or "")
    if body.caps_count is not None:
        row.manual_caps_count = body.caps_count
    session.commit()
    return image_dict(row)


def cap_stack_learning_queries(session: Session, tenant: str) -> list[str]:
    """Score keyword families by human feedback, keeping exploration alive."""
    votes: dict[str, list[int]] = {query.casefold(): [0, 0] for query in SCOUT_SEARCHES}
    rows = list(session.execute(select(
        RrugcCapStackImageModel.source_query, RrugcCapStackImageModel.manual_label
    ).where(
        RrugcCapStackImageModel.tenant_id == tenant,
        RrugcCapStackImageModel.manual_label.in_(("good", "bad", "ai")),
    ).order_by(RrugcCapStackImageModel.reviewed_at.desc()).limit(1500)))
    for source_query, label in rows:
        key = str(source_query or "").strip().casefold()
        if key in votes:
            votes[key][0 if label == "good" else 1] += 1
    ranked = sorted(SCOUT_SEARCHES, key=lambda query: (
        (votes[query.casefold()][0] + 1) /
        (sum(votes[query.casefold()]) + 3)
        - votes[query.casefold()][1] * .008
    ), reverse=True)
    # Stable first keyword anchors the user-requested origin. Remaining
    # queries learn from approval yield; protected 20% exploration slot.
    trailing = [q for q in ranked if q != "Cap Stack"]
    return ["Cap Stack"] + trailing[:3]


def control_for_run(session: Session, tenant: str, agent_id: str, run_id: str):
    row = get_control(session, tenant, lock=True)
    if (not row or not row.enabled or row.lease_agent_id != agent_id
            or row.lease_run_id != run_id or not utc_aware(row.lease_expires_at)
            or utc_aware(row.lease_expires_at) <= utc_now()):
        raise HTTPException(409, "Cap Stack lease missing, expired or disabled.")
    return row


@router.post("/scout-agents/{agent_id}/claim")
def claim_cap_stack(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = get_control(session, agent.tenant_id, lock=True)
    now = utc_now()
    if (not control or not control.enabled
            or (utc_aware(control.next_scan_at) and utc_aware(control.next_scan_at) > now)
            or (utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > now)):
        return None
    control.lease_agent_id = agent_id
    control.lease_run_id = str(uuid4())
    control.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
    control.run_created = 0
    session.commit()
    rows = list(session.scalars(select(RrugcCapStackImageModel.pin_url).where(
        RrugcCapStackImageModel.tenant_id == agent.tenant_id
    ).order_by(RrugcCapStackImageModel.created_at.desc()).limit(3500)))
    seed_rows = list(session.scalars(select(RrugcCapStackImageModel).where(
        RrugcCapStackImageModel.tenant_id == agent.tenant_id,
        RrugcCapStackImageModel.manual_label == "good",
    ).order_by(RrugcCapStackImageModel.reviewed_at.desc()).limit(8)))
    return {
        "run": {"id": control.lease_run_id}, "campaign_id": "cap-stack-shared", "scout_type": "cap_stack",
        "query": "Cap Stack", "search_queries": cap_stack_learning_queries(session, agent.tenant_id),
        "target_count": LANE_TARGET, "max_scroll_batches": 6,
        "progress": 0, "pipeline_count": 0, "source_context": None,
        "known_pin_urls": rows,
        "related_seeds": [{"pin_url": row.pin_url, "image_url": row.image_url,
                            "alt_text": row.alt_text} for row in seed_rows],
    }


@router.post("/scout-agents/{agent_id}/runs/{run_id}/heartbeat")
def heartbeat_cap_stack(
    agent_id: str, run_id: str, body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    control.lease_expires_at = utc_now() + timedelta(minutes=LEASE_MINUTES)
    session.commit()
    return {"ok": True}


@router.post("/scout-agents/{agent_id}/runs/{run_id}/candidates")
def submit_cap_stack(
    agent_id: str, run_id: str, body: ScoutBatch,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    created = 0
    existing = 0
    for item in body.items:
        key = pin_key(item.pin_url)
        validate_image_url(item.image_url)
        if session.scalar(select(RrugcCapStackImageModel.id).where(
            RrugcCapStackImageModel.tenant_id == agent.tenant_id,
            RrugcCapStackImageModel.pin_key == key,
        )):
            existing += 1
            continue
        session.add(RrugcCapStackImageModel(
            id=str(uuid4()), tenant_id=agent.tenant_id,
            pin_key=key, pin_url=item.pin_url, image_url=item.image_url,
            alt_text=item.alt_text, source_query=body.source_query,
            discovered_by_agent_id=agent_id, count_status="pending",
        ))
        created += 1
    control.run_created += created
    control.lease_expires_at = utc_now() + timedelta(minutes=LEASE_MINUTES)
    session.commit()
    return {
        "created": created, "existing": existing, "progress": control.run_created,
        "pipeline_count": control.run_created, "target_count": LANE_TARGET,
        "campaign_status": "running" if control.run_created < LANE_TARGET else "complete",
    }


@router.post("/scout-agents/{agent_id}/runs/{run_id}/complete")
def complete_cap_stack(
    agent_id: str, run_id: str, body: CompleteRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = control_for_run(session, agent.tenant_id, agent_id, run_id)
    control.lease_run_id = None
    control.lease_agent_id = None
    control.lease_expires_at = None
    control.last_result = body.status
    # A failed or login-blocked Pinterest session must cool down, not thrash.
    pause = 900 if body.status == "completed" else 1800
    control.next_scan_at = utc_now() + timedelta(seconds=pause)
    session.commit()
    return {"status": body.status, "created": control.run_created}


@router.get("/scout-agents/{agent_id}/status")
def cap_stack_agent_status(
    agent_id: str, authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    agent = agent_auth(session, agent_id, authorization)
    control = get_control(session, agent.tenant_id)
    return {
        "enabled": bool(control and control.enabled),
        "lease_active": bool(control and utc_aware(control.lease_expires_at) and utc_aware(control.lease_expires_at) > utc_now()),
        "last_result": control.last_result if control else None,
        "next_scan_at": control.next_scan_at if control else None,
    }
