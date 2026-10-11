"""Authenticated, tenant-scoped Research Inbox routes.

External research is only a suggestion. These routes never fetch external URLs,
import browser cookies, execute agent-generated content or call AEBrowse.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.scout_automation import RrugcAutoScoutService
from app.modules.realistic_review_ugc.service import RrugcError
from app.modules.realistic_review_ugc.research_discovery import (
    AGENT_NAME, discovery_status, set_discovery_pause,
)
from app.modules.realistic_review_ugc.research_intelligence import (
    ResearchValidationError, import_signals, list_signals,
    review_signal, stage0_export, apply_approved_to_stage0,
)

router = APIRouter(prefix="/api/v1/realistic-review-ugc/research",
                   tags=["realistic-review-ugc", "research-intelligence"])
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")


class ResearchSignalInput(BaseModel):
    source: Literal["rss", "github", "youtube", "web", "pinterest", "etsy", "amazon"]
    channel: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    url: str = Field(min_length=8, max_length=2048)
    excerpt: str = Field(default="", max_length=480)


class ResearchImportRequest(BaseModel):
    signals: list[ResearchSignalInput] = Field(min_length=1, max_length=50)


class DiscoveryControlRequest(BaseModel):
    paused: bool


class ResearchReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    keyword: str | None = Field(default=None, max_length=150)


def enabled() -> None:
    if os.getenv("CAM_RESEARCH_INTELLIGENCE_ENABLED", "1") != "1":
        raise HTTPException(status_code=404, detail="Research Intelligence is disabled.")


def invalid(exc: ResearchValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/scout-agents/{agent_id}/signals/import")
def import_agent_research_signals(
    agent_id: str,
    request: ResearchImportRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    """Paired Scout Agent ingestion; tenant resolved from authenticated agent.

    No user cookie is needed for a background worker. Agent token cannot
    change review status or trigger paid Stage 0 provider calls.
    """
    enabled()
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Missing agent Bearer token.")
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=authorization[7:].strip(),
        )
    except RrugcError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": "Invalid scout agent credentials."}) from exc
    try:
        return import_signals(
            session, tenant_id=agent.tenant_id, user_id="scout-agent:" + agent.id,
            signals=[signal.model_dump() for signal in request.signals],
        )
    except ResearchValidationError as exc:
        session.rollback()
        raise invalid(exc) from exc


@router.get("/auto-discover/status")
def get_auto_discover_status(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    enabled()
    return discovery_status(session, principal.active_tenant_id)


@router.post("/auto-discover/control")
def control_auto_discover(
    request: DiscoveryControlRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    enabled()
    try:
        return set_discovery_pause(session, principal.active_tenant_id, request.paused)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/scout-agents/{agent_id}/auto-discover/config")
def get_auto_discover_config(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    enabled()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing agent Bearer token")
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=authorization[7:].strip(),
        )
    except RrugcError as exc:
        raise HTTPException(status_code=exc.status_code, detail="Invalid scout agent") from exc
    if agent.name != AGENT_NAME:
        raise HTTPException(status_code=403, detail="Dedicated research pairing required")
    return {"enabled": agent.status != "paused", "platforms": ["etsy", "amazon", "pinterest"]}


@router.get("/scout-agents/{agent_id}/auto-discover/status")
def get_agent_auto_discover_status(
    agent_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    enabled()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing agent Bearer token")
    try:
        agent = RrugcAutoScoutService(session).authenticate_agent(
            agent_id=agent_id, raw_token=authorization[7:].strip(),
        )
    except RrugcError as exc:
        raise HTTPException(status_code=exc.status_code, detail="Invalid scout agent") from exc
    return discovery_status(session, agent.tenant_id)


@router.get("/signals")
def research_signals(
    status: Literal["pending", "approved", "rejected", "all"] = Query(default="pending"),
    limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    enabled()
    return {"items": list_signals(session, tenant_id=principal.active_tenant_id,
                                   status=status, limit=limit)}


@router.post("/signals/import")
def import_research_signals(
    request: ResearchImportRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    enabled()
    try:
        return import_signals(session, tenant_id=principal.active_tenant_id,
                              user_id=principal.user_id,
                              signals=[signal.model_dump() for signal in request.signals])
    except ResearchValidationError as exc:
        session.rollback()
        raise invalid(exc) from exc


@router.post("/signals/{signal_id}/review")
def review_research_signal(
    signal_id: str,
    request: ResearchReviewRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    enabled()
    try:
        return review_signal(session, tenant_id=principal.active_tenant_id,
                             user_id=principal.user_id, signal_id=signal_id,
                             decision=request.decision, keyword=request.keyword)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Signal not found.") from exc
    except ResearchValidationError as exc:
        session.rollback()
        raise invalid(exc) from exc


@router.post("/stage0-apply")
def apply_research_keywords_to_stage0(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    """Idempotently backfill previously approved keywords, without paid lookup."""
    enabled()
    return apply_approved_to_stage0(session, tenant_id=principal.active_tenant_id)


@router.get("/stage0-export")
def export_research_keywords(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    enabled()
    return stage0_export(session, tenant_id=principal.active_tenant_id)
