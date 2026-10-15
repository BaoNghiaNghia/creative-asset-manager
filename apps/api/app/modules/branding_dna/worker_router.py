"""Dedicated Branding DNA agent enrollment and heartbeat (control plane only).

There is deliberately NO executable shop task dispatcher. Enrollment and
heartbeat cannot start Playwright, run a scan or access external URLs.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandWorkerModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-workers"])
CONFIGURE = require_permission("ai_provider.configure")
WORKER_TOKEN_BYTES = 32
HEARTBEAT_STALE_AFTER = timedelta(minutes=3)

class WorkerRegister(BaseModel):
    label: str = Field(min_length=3, max_length=90, pattern=r"^[\w\- .()]{3,90}$")

class WorkerHeartbeat(BaseModel):
    status: str = Field(default="idle", pattern="^(idle|paused|unhealthy)$")
    agent_version: str = Field(default="0.1.0", min_length=1, max_length=64)

def worker_online(row: BrandWorkerModel, now: datetime | None = None) -> bool:
    if not row.enabled or row.last_heartbeat is None:
        return False
    last = row.last_heartbeat
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - last <= HEARTBEAT_STALE_AFTER

def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def authenticate_agent(session: Session, agent_id: str, auth_header: str | None) -> BrandWorkerModel:
    raw = (auth_header or "").removeprefix("Bearer ")
    # Reject cookie auth: API worker endpoints only accept isolated Bearer tokens.
    if not (auth_header or "").startswith("Bearer ") or len(raw) < 42 or len(raw) > 256:
        raise HTTPException(401, detail="Invalid worker credentials")
    agent = session.get(BrandWorkerModel, agent_id)
    expected = agent.token_hash if agent is not None and agent.token_hash else "0" * 64
    if not hmac.compare_digest(token_digest(raw), expected) or agent is None or not agent.enabled:
        raise HTTPException(401, detail="Invalid worker credentials")
    return agent

@router.post("/workers", status_code=201)
def register_worker(payload: WorkerRegister, response: Response,
                    session: Session = Depends(get_db),
                    principal: CurrentPrincipal = Depends(CONFIGURE)):
    token = secrets.token_urlsafe(WORKER_TOKEN_BYTES)
    agent = BrandWorkerModel(
        tenant_id=principal.active_tenant_id, label=payload.label,
        enabled=True, status="offline", token_hash=token_digest(token),
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    response.headers["Cache-Control"] = "no-store"
    return {"id": agent.id, "label": agent.label,
            "status": "offline", "enabled": True,
            "agent_token": token,
            "message": "Token shown once. Store in the Windows worker secret store."}

@router.post("/workers/{agent_id}/disable")
def disable_worker(agent_id: str, session: Session = Depends(get_db),
                   principal: CurrentPrincipal = Depends(CONFIGURE)):
    agent = session.scalar(select(BrandWorkerModel).where(
        BrandWorkerModel.id == agent_id,
        BrandWorkerModel.tenant_id == principal.active_tenant_id,
    ))
    if agent is None:
        raise HTTPException(404, detail="Worker not found")
    agent.enabled = False
    agent.token_hash = None
    agent.status = "disabled"
    session.commit()
    return {"id": agent.id, "enabled": False, "status": "disabled"}

@router.post("/workers/{agent_id}/heartbeat")
def worker_heartbeat(agent_id: str, data: WorkerHeartbeat,
                     authorization: str | None = Header(default=None),
                     session: Session = Depends(get_db)):
    agent = authenticate_agent(session, agent_id, authorization)
    agent.last_heartbeat = datetime.now(timezone.utc)
    agent.status = data.status
    agent.agent_version = data.agent_version
    session.commit()
    return {"status": "accepted", "scan_enabled": False,
            "next_heartbeat_seconds": 60}

@router.post("/workers/{agent_id}/lease")
def worker_lease(agent_id: str, authorization: str | None = Header(default=None),
                 session: Session = Depends(get_db)):
    # Explicitly return no jobs until the scan task model and allowed adapters
    # pass production security review. A connected agent != an enabled crawler.
    authenticate_agent(session, agent_id, authorization)
    return {"job": None, "scan_enabled": False,
            "reason": "scan_pipeline_not_deployed", "retry_after_seconds": 60}
