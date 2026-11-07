"""Disabled-by-default read-only Agent Harness administration surface.

This module exposes no registration, promotion, queue dispatch, agent spawn,
secret or arbitrary tool execution endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.authorization.principal import (
    CurrentPrincipal, require_permission, require_tenant_scope,
)

from .repository import AgentHarnessRepository


router = APIRouter(prefix="/api/v1/admin/agent-harness", tags=["agent-harness"])
READ = require_permission("ai_operations.read")


def _require_read_flag() -> None:
    if not get_settings().AGENT_HARNESS_READ_API_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/{tenant_id}/overview")
def read_agent_overview(
    tenant_id: str,
    principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    repository = AgentHarnessRepository(db)
    agents = repository.list_definitions(tenant_id)
    return {
        "tenant_id": tenant_id,
        "execution_enabled": False,  # Deliberately hard-off in Phase 2.
        "agent_count": len(agents),
        "agents": [
            {
                "agent_id": item.agent_id,
                "version": item.version,
                "enabled": item.enabled,
                "capabilities": item.capabilities_json,
                "preferred_executor": item.preferred_executor,
            }
            for item in agents
        ],
        "experience_counts": repository.experience_counts(tenant_id),
    }
