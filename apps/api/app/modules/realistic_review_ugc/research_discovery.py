"""Tenant-scoped read-only status and fenced controls for the VPS research agent.

Uses the existing paired Scout identity: no new migration, no shared Windows
credentials, and no flag that pretends an unpaired worker is active.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .model import RrugcScoutAgentModel, RrugcResearchSignalModel

AGENT_NAME = "Agent-Reach Auto Discover"
PLATFORMS = ("pinterest", "amazon", "etsy")


def research_agent(session: Session, tenant_id: str) -> RrugcScoutAgentModel | None:
    return session.scalar(
        select(RrugcScoutAgentModel).where(
            RrugcScoutAgentModel.tenant_id == tenant_id,
            RrugcScoutAgentModel.active.is_(True),
            RrugcScoutAgentModel.name == AGENT_NAME,
        ).order_by(RrugcScoutAgentModel.created_at.desc()).limit(1)
    )


def discovery_status(session: Session, tenant_id: str) -> dict:
    agent = research_agent(session, tenant_id)
    now = datetime.now(timezone.utc)
    online = False
    if agent is not None and agent.last_seen_at is not None:
        seen = agent.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        online = seen >= now - timedelta(hours=3)
    status = ("not_configured" if agent is None else
              "paused" if agent.status == "paused" else
              agent.status if online and agent.status in ("ready", "busy", "error") else "offline")
    values = dict(session.execute(
        select(RrugcResearchSignalModel.source, func.count()).where(
            RrugcResearchSignalModel.tenant_id == tenant_id,
            RrugcResearchSignalModel.channel == "auto-discover",
        ).group_by(RrugcResearchSignalModel.source)
    ).all())
    pending = session.scalar(select(func.count()).select_from(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.tenant_id == tenant_id,
        RrugcResearchSignalModel.channel == "auto-discover",
        RrugcResearchSignalModel.status == "pending",
    )) or 0
    promoted = session.scalar(select(func.count()).select_from(RrugcResearchSignalModel).where(
        RrugcResearchSignalModel.tenant_id == tenant_id,
        RrugcResearchSignalModel.channel == "auto-discover",
        RrugcResearchSignalModel.status == "approved",
        RrugcResearchSignalModel.reviewed_by_user_id.like("auto:agent-reach:%"),
    )) or 0
    return {
        "state": status, "connected": agent is not None,
        "server": "VPS", "source": "Agent-Reach / Web Search",
        "last_seen_at": agent.last_seen_at.isoformat() if agent and agent.last_seen_at else None,
        "last_error_code": agent.last_error_code if agent else None,
        "total_evidence": sum(values.values()),
        "pending_review": int(pending),
        "stage0_auto_keywords": int(promoted),
        "platforms": {name: int(values.get(name, 0)) for name in PLATFORMS},
    }


def set_discovery_pause(session: Session, tenant_id: str, paused: bool) -> dict:
    # Mutate only the dedicated server agent, never another Scout's status.
    agent = research_agent(session, tenant_id)
    if agent is None:
        raise LookupError("Pair the dedicated Agent-Reach Auto Discover Scout first")
    agent.status = "paused" if paused else "offline"  # Resume is not proof of online service.
    agent.updated_at = datetime.now(timezone.utc)
    session.commit()
    return discovery_status(session, tenant_id)
