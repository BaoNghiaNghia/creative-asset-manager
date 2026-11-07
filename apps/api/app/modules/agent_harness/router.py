"""Feature-gated, tenant-scoped Agent Harness catalog.

Registration is metadata-only, always disabled, version-immutable and audited
in the SAME database transaction. No agent runs, no credential is provisioned,
and no Experience candidate can be promoted by these endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import (
    CurrentPrincipal, require_all_permissions, require_permission, require_tenant_scope,
)

from .executor_readiness import (
    ExecutorSnapshot, browser_snapshot, preview_route, probe_codex,
)
from .registry import AgentRegistryError
from .repository import AgentHarnessRepository
from .schema import AgentCatalogRegistration, AgentRoutePreviewRequest


router = APIRouter(prefix="/api/v1/admin/agent-harness", tags=["agent-harness"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")
READ_CONFIGURE = require_all_permissions("ai_operations.read", "ai_provider.configure")


def _require_read_flag() -> None:
    if not get_settings().AGENT_HARNESS_READ_API_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


def _require_management_flag() -> None:
    settings = get_settings()
    if not (settings.AGENT_HARNESS_READ_API_ENABLED
            and settings.AGENT_HARNESS_MANAGEMENT_API_ENABLED):
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
        "execution_enabled": False,  # Deliberately hard-off in Phase 2C.
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


@router.post("/{tenant_id}/agents", status_code=status.HTTP_201_CREATED)
def register_agent_definition(
    tenant_id: str,
    body: AgentCatalogRegistration,
    response: Response,
    principal: CurrentPrincipal = Depends(CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Store an inert agent definition, with same-transaction actor audit.

    Changing an existing version or pretending an executor is available is
    never allowed. Retries of identical registrations are idempotent.
    """
    _require_management_flag()
    require_tenant_scope(principal, tenant_id)

    repo = AgentHarnessRepository(db)
    existing = repo.get_definition(tenant_id, body.agent_id, body.version)
    if existing is not None:
        # Even idempotent retries are validated against the stored, frozen version.
        try:
            row = repo.register_definition(tenant_id, body.definition(), actor_id=principal.user_id)
        except (ValueError, AgentRegistryError) as exc:
            raise HTTPException(status_code=409, detail="Immutable agent version conflict") from exc
        response.status_code = status.HTTP_200_OK
        return {
            "tenant_id": tenant_id, "agent_id": row.agent_id, "version": row.version,
            "enabled": False, "created": False, "execution_enabled": False,
        }

    try:
        row = repo.register_definition(tenant_id, body.definition(), actor_id=principal.user_id)
        db.add(AuthAuditEventModel(
            tenant_id=tenant_id,
            actor_id=principal.user_id,
            action="agent_catalog_registered",
            detail_json={
                "agent_id": row.agent_id,
                "version": row.version,
                "preferred_executor": row.preferred_executor,
                "capabilities": row.capabilities_json,
                "reason": body.reason,
                "execution_enabled": False,
            },
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Agent version already registered") from exc
    except (ValueError, AgentRegistryError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="Invalid agent definition") from exc
    except Exception:
        db.rollback()
        raise
    return {
        "tenant_id": tenant_id, "agent_id": row.agent_id, "version": row.version,
        "enabled": False, "created": True, "execution_enabled": False,
    }


@router.post("/{tenant_id}/agents/{agent_id}/{version}/route-preview")
def preview_agent_route(
    tenant_id: str,
    agent_id: str,
    version: str,
    body: AgentRoutePreviewRequest,
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Read-only bounded preview: no job, lease, queue or model execution."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    agent = AgentHarnessRepository(db).get_definition(tenant_id, agent_id, version)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent version not registered")

    capabilities = frozenset(body.required_capabilities)
    tools = frozenset(body.required_tools)
    settings = get_settings()

    # Do not run even the bounded local CLI login-status probe when the
    # request violates the tenant's agent capability/tool policy.
    allowed = (
        capabilities.issubset(set(agent.capabilities_json))
        and tools.issubset(set(agent.allowed_tools_json))
        and not settings.AI_EMERGENCY_STOP_ENABLED
    )
    if allowed:
        codex = probe_codex(
            db, tenant_id, body.stage,
            codex_home=settings.CODEX_IMAGE_HOME,
            codex_binary=settings.CODEX_IMAGE_BINARY,
        )
    else:
        codex = ExecutorSnapshot("codex", "not_probed", 0, 0, 0, frozenset())
    return preview_route(
        agent, stage=body.stage,
        required_capabilities=capabilities, required_tools=tools,
        codex=codex, browser=browser_snapshot(),
        global_emergency_stop=settings.AI_EMERGENCY_STOP_ENABLED,
    )
