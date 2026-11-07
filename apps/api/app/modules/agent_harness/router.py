"""Feature-gated, tenant-scoped Agent Harness catalog.

Registration is metadata-only, always disabled, version-immutable and audited
in the SAME database transaction. No agent runs, no credential is provisioned,
and no Experience candidate can be promoted by these endpoints.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.processing.model import OutboxEventModel
from app.modules.authorization.principal import (
    CurrentPrincipal, require_all_permissions, require_permission, require_tenant_scope,
)

from .evidence_audit import audit_stage1_candidates
from .holdout import evaluation_readiness_from_audit
from .executor_readiness import (
    ExecutorSnapshot, browser_snapshot, preview_route, probe_codex,
)
from .model import AgentExperienceModel
from .registry import AgentRegistryError
from .repository import AgentHarnessRepository, _event_key
from .schema import AgentCatalogRegistration, AgentRoutePreviewRequest, Stage1FeedbackSyncRequest
from .stage1_feedback import AGENT_ID as FEEDBACK_AGENT_ID, SOURCE_VERSION, snapshots


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


@router.get("/{tenant_id}/feedback/stage1/evaluation-readiness")
def read_stage1_evaluation_readiness(
    tenant_id: str,
    cohort: Literal["music", "western", "pets", "healthcare", "coffee", "family", "outdoors"],
    limit: int = Query(default=20, ge=1, le=30),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Preflight only. Historical Pick/Reject is NOT an independent holdout."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    audit = audit_stage1_candidates(
        db, tenant_id=tenant_id, cohort=cohort, limit=limit,
    )
    return evaluation_readiness_from_audit(audit)


@router.get("/{tenant_id}/feedback/stage1/evidence-audit")
def audit_stage1_feedback_evidence(
    tenant_id: str,
    cohort: Literal["music", "western", "pets", "healthcare", "coffee", "family", "outdoors"],
    limit: int = Query(default=20, ge=1, le=30),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Review bounded source provenance; never promote or start an agent."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    return audit_stage1_candidates(
        db, tenant_id=tenant_id, cohort=cohort, limit=limit,
    )


@router.get("/{tenant_id}/feedback/stage1/outbox-status")
def read_stage1_feedback_outbox_status(
    tenant_id: str,
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated queue status only; never expose vote payloads or identities."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    from .feedback_outbox import EVENT_TYPE
    counts = {status: 0 for status in ("pending", "processing", "published", "failed")}
    rows = db.execute(
        select(OutboxEventModel.status, func.count())
        .where(
            OutboxEventModel.tenant_id == tenant_id,
            OutboxEventModel.event_type == EVENT_TYPE,
        )
        .group_by(OutboxEventModel.status)
    ).all()
    for state, count in rows:
        if state in counts:
            counts[state] = int(count)
    config = get_settings()
    return {
        "tenant_id": tenant_id,
        "outbox_event_type": EVENT_TYPE,
        "counts": counts,
        "auto_capture_enabled": bool(config.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED
                                     and config.AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED),
        "auto_process_enabled": bool(config.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED
                                     and config.AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED),
        "execution_enabled": False,
        "automatic_promotion_enabled": False,
    }


@router.post("/{tenant_id}/feedback/stage1/candidates")
def sync_stage1_feedback_candidates(
    tenant_id: str,
    body: Stage1FeedbackSyncRequest,
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Derive only inert candidate snapshots from existing Stage 1 votes.

    Default dry run is read-only; explicit sync requires a separately enabled
    flag, reviewed dormant feedback agent version, actor and tenant RBAC.
    """
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    settings = get_settings()
    if settings.AI_EMERGENCY_STOP_ENABLED:
        raise HTTPException(status_code=503, detail="AI operations emergency stop")
    if not body.dry_run and not (
        settings.AGENT_HARNESS_MANAGEMENT_API_ENABLED
        and settings.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED
    ):
        raise HTTPException(status_code=404, detail="Not found")

    summaries = snapshots(
        db, tenant_id=tenant_id,
        cohort=body.cohort, agent_version=body.agent_version,
    )
    result = {
        "tenant_id": tenant_id,
        "cohort": body.cohort,
        "source_version": SOURCE_VERSION,
        "dry_run": body.dry_run,
        "eligible_patterns": len(summaries),
        "created_candidates": 0,
        "candidate_only": True,
        "can_promote": False,
        "agent_execution_enabled": False,
        "patterns": [item.summary() for item in summaries],
    }
    if body.dry_run:
        return result

    repository = AgentHarnessRepository(db)
    agent = repository.get_definition(tenant_id, FEEDBACK_AGENT_ID, body.agent_version)
    if (agent is None
            or agent.enabled
            or "feedback_analyze" not in agent.capabilities_json
            or "feedback.read" not in agent.allowed_tools_json):
        raise HTTPException(
            status_code=409,
            detail="Register a disabled, read-only feedback intelligence agent version first",
        )
    created = 0
    try:
        for item in summaries:
            candidate = item.candidate
            key = _event_key(candidate)
            existing = db.get(AgentExperienceModel, (tenant_id, key))
            row = repository.record_candidate(candidate)
            if existing is not None:
                if existing.status != "candidate":
                    raise ValueError("Feedback snapshot already has a different status")
                continue
            row.evaluation_json = {
                "source_kind": SOURCE_VERSION,
                "evidence_status": "unverified_human_preference",
                "distinct_keyword_count": item.distinct_keywords,
                "holdout_verified": False,
                "independent_qa_passed": False,
                "promotion_allowed": False,
            }
            created += 1
        if created:
            db.add(AuthAuditEventModel(
                tenant_id=tenant_id,
                actor_id=principal.user_id,
                action="agent_stage1_feedback_candidates_synced",
                detail_json={
                    "source_version": SOURCE_VERSION,
                    "cohort": body.cohort,
                    "agent_id": FEEDBACK_AGENT_ID,
                    "agent_version": body.agent_version,
                    "candidate_count": created,
                    "promotion_allowed": False,
                },
            ))
            db.commit()
        else:
            db.rollback()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Candidate snapshot conflict") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Candidate snapshot conflict") from exc
    except Exception:
        db.rollback()
        raise
    result["created_candidates"] = created
    return result


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
