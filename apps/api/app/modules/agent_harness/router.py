"""Feature-gated, tenant-scoped Agent Harness catalog.

Registration is metadata-only, always disabled, version-immutable and audited
in the SAME database transaction. No agent runs, no credential is provisioned,
and no Experience candidate can be promoted by these endpoints.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
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
from .model import AgentExperienceModel, AgentHoldoutStudyModel, AgentHoldoutAssignmentModel
from .registry import AgentRegistryError
from .repository import AgentHarnessRepository, _event_key
from .schema import AgentCatalogRegistration, AgentRoutePreviewRequest, Stage1FeedbackSyncRequest
from .trial_schema import HoldoutStudyRegistration, HoldoutKeywordReservation, TrialArtworkSnapshotCapture
from .trial_registration import HoldoutStudyConflict, register_study, reserve_keywords
from .trial_evidence_status import read_stage1_trial_evidence
from .trial_artwork_lineage import (
    TrialArtworkLineageError, artwork_lineage, public_lineage_status,
)
from .trial_artwork_bytes import verify_stored_artwork_bytes
from .trial_artwork_snapshot import (
    TrialArtworkSnapshotError, capture_snapshot, get_snapshot, snapshot_status,
)
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


def _require_trial_registration_flag() -> None:
    settings = get_settings()
    if not (
        settings.AGENT_HARNESS_READ_API_ENABLED
        and settings.AGENT_HARNESS_MANAGEMENT_API_ENABLED
        and settings.AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED
        and not getattr(settings, "AI_EMERGENCY_STOP_ENABLED", False)
    ):
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/{tenant_id}/trials/stage1", status_code=status.HTTP_201_CREATED)
def register_stage1_holdout_study(
    tenant_id: str,
    body: HoldoutStudyRegistration,
    response: Response,
    principal: CurrentPrincipal = Depends(CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Register only an inert, immutable, evidence-linked study."""
    _require_trial_registration_flag()
    require_tenant_scope(principal, tenant_id)
    try:
        result, created = register_study(
            db, tenant_id=tenant_id, body=body, actor_id=principal.user_id,
        )
        if not body.dry_run and created:
            db.add(AuthAuditEventModel(
                tenant_id=tenant_id, actor_id=principal.user_id,
                action="agent_holdout_study_registered",
                detail_json={
                    "study_id": body.study_id, "cohort": body.cohort,
                    "trial_version": body.trial_version, "holdout_percent": body.holdout_percent,
                    "reason": body.reason, "trial_active": False,
                },
            ))
            db.commit()
        else:
            response.status_code = status.HTTP_200_OK
    except HoldoutStudyConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="immutable_study_conflict") from exc
    except Exception:
        db.rollback()
        raise
    return {**result, "created": created, "dry_run": body.dry_run,
            "trial_active": False, "generation_enabled": False,
            "automatic_promotion_enabled": False}


@router.post("/{tenant_id}/trials/stage1/{study_id}/reservations")
def reserve_stage1_trial_keywords(
    tenant_id: str,
    body: HoldoutKeywordReservation,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    principal: CurrentPrincipal = Depends(CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Reserve keyword arms before any Stage 1 generation exists."""
    _require_trial_registration_flag()
    require_tenant_scope(principal, tenant_id)
    try:
        result = reserve_keywords(
            db, tenant_id=tenant_id, study_id=study_id, body=body,
            actor_id=principal.user_id,
        )
        if not body.dry_run and result.new_assignments:
            db.add(AuthAuditEventModel(
                tenant_id=tenant_id, actor_id=principal.user_id,
                action="agent_holdout_keywords_reserved",
                detail_json={
                    "study_id": study_id, "new_assignments": result.new_assignments,
                    "reason": body.reason, "trial_active": False,
                },
            ))
            db.commit()
    except HoldoutStudyConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="keyword_assignment_conflict") from exc
    except Exception:
        db.rollback()
        raise
    return {
        "tenant_id": tenant_id, "study_id": study_id,
        "dry_run": body.dry_run, **result.summary(),
        "automatic_promotion_enabled": False,
    }


@router.get("/{tenant_id}/trials/stage1/{study_id}")
def read_stage1_trial_registration(
    tenant_id: str,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Read tenant-only planning status; no keyword identities in the summary."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    study = db.get(AgentHoldoutStudyModel, (tenant_id, study_id))
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    counts = {"control": 0, "challenger": 0}
    rows = db.execute(
        select(AgentHoldoutAssignmentModel.arm, func.count())
        .where(
            AgentHoldoutAssignmentModel.tenant_id == tenant_id,
            AgentHoldoutAssignmentModel.study_id == study_id,
        ).group_by(AgentHoldoutAssignmentModel.arm)
    ).all()
    for arm, count in rows:
        if arm in counts:
            counts[arm] = int(count)
    return {
        "tenant_id": tenant_id, "study_id": study_id,
        "cohort": study.cohort, "status": study.status,
        "trial_version": study.trial_version,
        "holdout_percent": study.holdout_percent,
        "assignment_counts": counts,
        "trial_active": False,
        "generation_enabled": False,
        "independent_qa_verified": False,
        "automatic_promotion_enabled": False,
    }


@router.get("/{tenant_id}/trials/stage1/{study_id}/evidence")
def read_trial_stage1_evidence_status(
    tenant_id: str,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Read receipt/QA counts, not improvement claims, without dispatching AI."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    try:
        return read_stage1_trial_evidence(
            db, tenant_id=tenant_id, study_id=study_id,
            settings=get_settings(),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Study not found") from None


@router.get("/{tenant_id}/trials/stage1/{study_id}/artwork/{output_version_id}/lineage")
def read_stage1_artwork_provenance(
    tenant_id: str,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    output_version_id: str = Path(min_length=36, max_length=36),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Read-only DB chain, NEVER independent image-byte or QA attestation."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    try:
        return public_lineage_status(artwork_lineage(
            db, tenant_id=tenant_id, study_id=study_id,
            output_version_id=output_version_id,
        ))
    except TrialArtworkLineageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/{tenant_id}/trials/stage1/{study_id}/artwork/{output_version_id}/verify-bytes")
async def verify_stage1_artwork_provenance_bytes(
    tenant_id: str,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    output_version_id: str = Path(min_length=36, max_length=36),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Explicit opt-in Drive read; capped bytes, no QA writes, no AI requests."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    settings = get_settings()
    if not (
        getattr(settings, "AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED", False)
        and settings.AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED
        and not settings.AI_EMERGENCY_STOP_ENABLED
    ):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        claim = artwork_lineage(
            db, tenant_id=tenant_id, study_id=study_id,
            output_version_id=output_version_id,
        )
        from app.modules.storage.provider_factory import build_managed_storage_provider
        from app.domain.providers.contracts import StorageProviderError
        try:
            result = await verify_stored_artwork_bytes(
                build_managed_storage_provider(settings), claim,
            )
        except StorageProviderError:
            raise HTTPException(
                status_code=503, detail="Stored artwork temporarily unavailable",
            ) from None
        # The output, Brief or exposure may have changed during Drive I/O:
        # re-read fresh DB state rather than accepting a stale cached claim.
        db.expire_all()
        if artwork_lineage(
            db, tenant_id=tenant_id, study_id=study_id,
            output_version_id=output_version_id,
        ) != claim:
            raise TrialArtworkLineageError("trial_artifact_lineage_changed")
        return result
    except TrialArtworkLineageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/{tenant_id}/trials/stage1/{study_id}/artwork/{output_version_id}/snapshot")
def read_stage1_artwork_snapshot(
    tenant_id: str,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    output_version_id: str = Path(min_length=36, max_length=36),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Read snapshot registry independently of deletable Output History rows."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    row = get_snapshot(db, tenant_id=tenant_id, study_id=study_id,
                       output_version_id=output_version_id)
    return snapshot_status(row)


@router.post("/{tenant_id}/trials/stage1/{study_id}/artwork/{output_version_id}/snapshot")
async def capture_stage1_artwork_snapshot(
    tenant_id: str,
    body: TrialArtworkSnapshotCapture,
    study_id: str = Path(min_length=4, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{3,63}$"),
    output_version_id: str = Path(min_length=36, max_length=36),
    principal: CurrentPrincipal = Depends(READ_CONFIGURE),
    db: Session = Depends(get_db),
) -> dict:
    """Reserve/verify a separate PNG copy. NO verdict, promotion or auto-capture."""
    _require_read_flag()
    require_tenant_scope(principal, tenant_id)
    settings = get_settings()
    if not (
        getattr(settings, "AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED", False)
        and settings.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED
        and settings.AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED
        and not settings.AI_EMERGENCY_STOP_ENABLED
    ):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        claim = artwork_lineage(
            db, tenant_id=tenant_id, study_id=study_id,
            output_version_id=output_version_id,
        )
        existing = get_snapshot(db, tenant_id=tenant_id, study_id=study_id,
                                output_version_id=output_version_id)
        if body.dry_run:
            return (
                {**snapshot_status(existing), "dry_run": True}
                if existing is not None
                else {
                    **snapshot_status(None), "dry_run": True,
                    "snapshot_status": "ready_for_explicit_capture",
                    "storage_bytes_verified": False,
                }
            )
        from app.modules.storage.provider_factory import build_managed_storage_provider
        from app.domain.providers.contracts import StorageProviderError
        try:
            result = await capture_snapshot(
                db, build_managed_storage_provider(settings),
                claim=claim, actor_id=principal.user_id,
            )
        except StorageProviderError:
            db.rollback()
            raise HTTPException(
                status_code=503, detail="Stored artwork temporarily unavailable",
            ) from None
        return {**result, "dry_run": False}
    except (TrialArtworkLineageError, TrialArtworkSnapshotError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None


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
