"""Stage 1 feedback capture and bounded candidate reconciliation.

Uses the existing processing outbox ledger, but NEVER claims generic outbox
events or schedules a model/ProcessingJob. Operator flags default to disabled.
All DB modifications are in the caller's transaction and can be rolled back.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.processing.model import OutboxEventModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.keyword_cohort_learning import COHORT_TERMS, semantic_cohort
from app.modules.realistic_review_ugc.model import RrugcImageOutputVersionModel as Output

from .model import AgentDefinitionModel, AgentExperienceModel
from .repository import AgentHarnessRepository, _event_key
from .stage1_feedback import AGENT_ID, snapshots

EVENT_TYPE = "agent.stage1-feedback.changed.v1"
ENTITY_TYPE = "rrugc_stage1_output"
MAX_EVENTS_PER_TICK = 24
MAX_COHORTS_PER_TICK = 3
MAX_EXISTING_EXPERIENCES_PER_SCOPE = 250


def record_stage1_feedback_change(
    session: Session, *, row: Output, keyword: str,
    previous_review: dict | None, current_review: dict,
) -> bool:
    """One same-transaction event per actual changed Pick/Reject/Undo.

    No event is emitted for re-clicking an identical decision and reason.
    The payload contains only a controlled cohort ID and timestamp; no
    keyword, actor, prompt, image, provider secret or client-supplied text.
    """
    if row.stage != "stage1" or not row.id or not row.tenant_id:
        return False
    prior = previous_review if isinstance(previous_review, dict) else {}
    if (
        prior.get("decision") == current_review.get("decision")
        and prior.get("reason") == current_review.get("reason")
    ):
        return False
    cohort = semantic_cohort(keyword)
    if cohort not in COHORT_TERMS:
        return False
    when = current_review.get("updated_at")
    decision = current_review.get("decision")
    if not isinstance(when, str) or decision not in ("picked", "rejected", "neutral"):
        raise ValueError("invalid source review")
    digest = hashlib.sha256(
        json.dumps(
            [row.tenant_id, row.id, when, decision, current_review.get("reason")],
            separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    ProcessingRepository(session).create_outbox_event(
        tenant_id=row.tenant_id,
        event_type=EVENT_TYPE,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        idempotency_key="agent:stage1:vote:" + digest,
        payload={"cohort": cohort, "review_updated_at": when},
        max_attempts=3,
    )
    return True


def capture_if_enabled(
    session: Session, *, row: Output, keyword: str,
    previous_review: dict | None, current_review: dict, settings: object,
) -> bool:
    """Single opt-in gate for both existing Stage 1 feedback endpoints."""
    if not (
        getattr(settings, "AGENT_HARNESS_FEEDBACK_SYNC_ENABLED", False)
        and getattr(settings, "AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED", False)
        and not getattr(settings, "AI_EMERGENCY_STOP_ENABLED", False)
    ):
        return False
    return record_stage1_feedback_change(
        session, row=row, keyword=keyword,
        previous_review=previous_review, current_review=current_review,
    )


@dataclass(frozen=True)
class DrainResult:
    processed_events: int = 0
    candidates_created: int = 0
    candidates_revoked: int = 0
    cohorts_reconciled: int = 0
    unconfigured_cohorts: int = 0


def _feedback_agent(session: Session, tenant_id: str) -> AgentDefinitionModel | None:
    """Only the latest registered, inactive, narrowly permissioned agent."""
    agents = list(session.scalars(
        select(AgentDefinitionModel).where(
            AgentDefinitionModel.tenant_id == tenant_id,
            AgentDefinitionModel.agent_id == AGENT_ID,
            AgentDefinitionModel.enabled.is_(False),
        ).order_by(
            AgentDefinitionModel.created_at.desc(),
            AgentDefinitionModel.version.desc(),
        ).limit(5)
    ).all())
    for agent in agents:
        if (
            "feedback_analyze" in agent.capabilities_json
            and agent.allowed_tools_json == ["feedback.read"]
        ):
            return agent
    return None


def drain_feedback_events(session: Session, *, limit: int = MAX_EVENTS_PER_TICK) -> DrainResult:
    """Process at most 24 of OUR outbox events and 3 cohort groups per tick.

    Postgres SKIP LOCKED prevents other operation workers from processing the
    same event. No generic outbox rows are claimed. No background AI calls.
    Caller must commit/rollback; errors leave events pending for retry.
    """
    limit = min(max(int(limit), 1), MAX_EVENTS_PER_TICK)
    query = select(OutboxEventModel).where(
        OutboxEventModel.event_type == EVENT_TYPE,
        OutboxEventModel.entity_type == ENTITY_TYPE,
        OutboxEventModel.status == "pending",
        OutboxEventModel.next_attempt_at <= datetime.now(timezone.utc),
    ).order_by(
        OutboxEventModel.created_at.asc(), OutboxEventModel.id.asc(),
    ).limit(limit)
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    events = list(session.scalars(query).all())
    if not events:
        return DrainResult()

    groups: dict[tuple[str, str], list[OutboxEventModel]] = defaultdict(list)
    invalid_events: list[OutboxEventModel] = []
    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        cohort = payload.get("cohort")
        if not event.tenant_id or cohort not in COHORT_TERMS:
            invalid_events.append(event)
            continue
        key = (event.tenant_id, cohort)
        if key not in groups and len(groups) >= MAX_COHORTS_PER_TICK:
            continue
        groups[key].append(event)

    now = datetime.now(timezone.utc)
    for event in invalid_events:
        event.status = "failed"
        event.last_error_code = "invalid_feedback_outbox_payload"
        event.last_error_message = "Invalid feedback event metadata"
        event.updated_at = now

    created = 0
    revoked = 0
    unconfigured = 0
    touched = len(invalid_events)
    repo = AgentHarnessRepository(session)
    for (tenant_id, cohort), batch in groups.items():
        # Never use the event payload as current feedback evidence. Re-read
        # persisted current source votes to respect Undo and later edits.
        agent = _feedback_agent(session, tenant_id)
        if agent is None:
            unconfigured += 1
            # Preserve the logged feedback event but do not retry indefinitely
            # or seed a privileged agent behind the operator's back.
            for event in batch:
                event.status = "failed"
                event.last_error_code = "feedback_agent_unconfigured"
                event.last_error_message = "Register inactive feedback agent before enabling auto-sync"
                event.updated_at = now
            touched += len(batch)
            continue

        current = snapshots(
            session, tenant_id=tenant_id, cohort=cohort,
            agent_version=agent.version,
        )
        by_rule = {x.candidate.rule_key: x.candidate for x in current}
        # Some historical candidates may have a different version. They must
        # never be accepted as current evidence without revalidation.
        old_rows = list(session.scalars(
            select(AgentExperienceModel).where(
                AgentExperienceModel.tenant_id == tenant_id,
                AgentExperienceModel.agent_id == AGENT_ID,
                AgentExperienceModel.scope_key == "cohort:" + cohort,
                AgentExperienceModel.status.in_(("candidate", "promoted")),
            ).order_by(
                AgentExperienceModel.created_at.desc(),
                AgentExperienceModel.event_key,
            ).limit(MAX_EXISTING_EXPERIENCES_PER_SCOPE)
        ).all())
        for prior in old_rows:
            fresh = by_rule.get(prior.rule_key) if prior.agent_version == agent.version else None
            still_valid = (
                fresh is not None
                and prior.event_key == _event_key(fresh)
                and prior.source_event_ids_json == list(fresh.event_ids)
                and prior.evidence_refs_json == list(fresh.evidence_refs)
            )
            if not still_valid:
                prior.status = "rejected"
                previous = dict(prior.evaluation_json or {})
                prior.evaluation_json = {
                    **previous,
                    "source_status": "stale_due_to_feedback_change",
                    "promotion_allowed": False,
                    "holdout_verified": False,
                    "independent_qa_passed": False,
                }
                revoked += 1

        # Explicitly flush revoked rows before evaluating unchanged evidence.
        session.flush()
        for summary in current:
            candidate = summary.candidate
            existing = repo.session.get(AgentExperienceModel, (tenant_id, _event_key(candidate)))
            if existing is not None:
                # Immutable exact-snapshot rows, including rejected rows, are
                # never resurrected by a new sweep.
                continue
            row = repo.record_candidate(candidate)
            row.evaluation_json = {
                "source_kind": "stage1-human-feedback-evidence-v1",
                "evidence_status": "unverified_human_preference",
                "distinct_keyword_count": summary.distinct_keywords,
                "holdout_verified": False,
                "independent_qa_passed": False,
                "promotion_allowed": False,
                "capture_mode": "durable-outbox",
            }
            created += 1

        for event in batch:
            event.status = "published"
            event.published_at = now
            event.updated_at = now
            event.last_error_code = None
            event.last_error_message = None
        touched += len(batch)

    session.flush()
    return DrainResult(
        processed_events=touched,
        candidates_created=created,
        candidates_revoked=revoked,
        cohorts_reconciled=len(groups) - unconfigured,
        unconfigured_cohorts=unconfigured,
    )
