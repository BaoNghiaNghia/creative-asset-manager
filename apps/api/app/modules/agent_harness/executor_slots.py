"""Inert Phase 4C.3A: PostgreSQL job-bound, fenced Image Agent slot primitives.

Intentionally NOT exposed by any HTTP route, queue worker, or Windows agent.
Callers own the database transaction and MUST commit job/slot state together.
A signed idle self-report does not meet this contract. Real browser admission
and rollout approval remain separate; no migration enables automatic dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
from app.modules.processing.model import ProcessingJobModel

from .model import AgentExecutorSlotModel

_JOB_TYPE = "etsy_design_extract"
_MAX_TTL_SECONDS = 120
_FUTURE_SKEW_SECONDS = 5


@dataclass(frozen=True)
class SlotDecision:
    state: str
    fencing_token: int | None = None
    expires_at: datetime | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _preflight(session: Session, *, enabled: bool) -> str | None:
    if enabled is not True:
        return "feature_disabled"
    if session.get_bind().dialect.name != "postgresql":
        # SELECT FOR UPDATE on SQLite is NOT a concurrency lock.
        return "postgresql_required"
    return None


def _expiry(now: datetime, job: ProcessingJobModel, ttl_seconds: int) -> datetime | None:
    expires = _utc(job.lease_expires_at)
    if expires is None or expires <= now:
        return None
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
        return None
    return min(expires, now + timedelta(seconds=ttl_seconds))


def _claimed_job(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    now: datetime,
) -> ProcessingJobModel | None:
    # Lock the ProcessingJob before the slot. This lock order is mandatory
    # for every future worker integration to prevent inversions/deadlocks.
    job = session.scalar(
        select(ProcessingJobModel).where(
            ProcessingJobModel.id == job_id,
            ProcessingJobModel.tenant_id == tenant_id,
        ).with_for_update()
    )
    if (
        job is None
        or job.job_type != _JOB_TYPE
        or job.status != "processing"
        or job.claimed_by != f"etsy-image:{agent_id}"
        or job.cancellation_requested
        or type(job.attempt_count) is not int
        or type(job.max_attempts) is not int
        or not 1 <= job.attempt_count <= job.max_attempts
        or _utc(job.claimed_at) is None
        or _utc(job.claimed_at) > now + timedelta(seconds=_FUTURE_SKEW_SECONDS)
        or _utc(job.lease_expires_at) is None
        or _utc(job.lease_expires_at) <= now
        or _utc(job.lease_expires_at) <= _utc(job.claimed_at)
    ):
        return None
    return job


def _same_claim(slot: AgentExecutorSlotModel, job: ProcessingJobModel) -> bool:
    """Match one claim epoch; the job ID alone can be reused on retry."""
    return bool(
        slot.job_id == job.id
        and slot.claim_attempt_count == job.attempt_count
        and _utc(slot.claim_started_at) == _utc(job.claimed_at)
    )


def _active_image_agent(session: Session, tenant_id: str, agent_id: str) -> bool:
    agent = session.scalar(
        select(EtsySpyAgent).where(
            EtsySpyAgent.id == agent_id,
            EtsySpyAgent.tenant_id == tenant_id,
        ).with_for_update(read=True)
    )
    return bool(
        agent is not None and agent.active
        and agent.name.startswith(IMAGE_AGENT_NAME_PREFIX)
    )


def reserve_existing_image_job(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    enabled: bool = False, emergency_stop: bool = True,
    ttl_seconds: int = 30, now: datetime | None = None,
) -> SlotDecision:
    """Reserve only an ALREADY CLAIMED job; never claim or dispatch it.

    PostgreSQL's unique device row + FOR UPDATE serialize attempts, including
    first allocation (INSERT ON CONFLICT waits). Other unresolved processing
    claims by this device fail closed, even if their old lease is stale.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return SlotDecision(blocked)
    if emergency_stop is not False:
        return SlotDecision("emergency_stop")
    clock = _utc(now) or datetime.now(timezone.utc)
    job = _claimed_job(
        session, tenant_id=tenant_id, agent_id=agent_id,
        job_id=job_id, now=clock,
    )
    expiry = _expiry(clock, job, ttl_seconds) if job else None
    if expiry is None:
        return SlotDecision("job_claim_not_verified")
    # All mutating APIs lock the source job before device then slot.
    if not _active_image_agent(session, tenant_id, agent_id):
        return SlotDecision("device_not_verified")
    # Conservatively refuse if another job is still marked processing,
    # regardless of its lease age. Human/queue reconciliation comes first.
    other = session.scalar(
        select(ProcessingJobModel.id).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == _JOB_TYPE,
            ProcessingJobModel.claimed_by == f"etsy-image:{agent_id}",
            ProcessingJobModel.status == "processing",
            ProcessingJobModel.id != job_id,
        ).limit(1)
    )
    if other is not None:
        return SlotDecision("unresolved_device_claim")

    # A prior device may still own this job's old claim epoch. Detect its
    # historical slot before inserting/updating ours: otherwise the ledger's
    # (tenant_id, job_id) unique constraint would abort the whole transaction.
    # Cross-device handoff requires a separate, audited reconciliation path;
    # an expired clock alone never authorizes reassignment of that job.
    occupied_by_other = session.scalar(
        select(AgentExecutorSlotModel.agent_id).where(
            AgentExecutorSlotModel.tenant_id == tenant_id,
            AgentExecutorSlotModel.job_id == job_id,
            AgentExecutorSlotModel.agent_id != agent_id,
        ).with_for_update()
    )
    if occupied_by_other is not None:
        return SlotDecision("job_reserved_elsewhere")

    session.execute(
        pg_insert(AgentExecutorSlotModel).values(
            tenant_id=tenant_id, agent_id=agent_id,
            fencing_token=0, updated_at=clock,
        ).on_conflict_do_nothing(index_elements=["tenant_id", "agent_id"])
    )
    slot = session.get(
        AgentExecutorSlotModel, (tenant_id, agent_id),
        with_for_update=True, populate_existing=True,
    )
    if slot is None:  # defense in depth against unexpected transaction state
        return SlotDecision("slot_not_found")
    if slot.job_id is not None:
        old_expires = _utc(slot.lease_expires_at)
        old_live = old_expires is not None and old_expires > clock
        if _same_claim(slot, job):
            if old_live:
                if old_expires > _utc(job.lease_expires_at):
                    return SlotDecision("slot_outlives_source_lease")
                return SlotDecision("already_reserved", slot.fencing_token, old_expires)
            return SlotDecision("expired_slot_requires_reconcile")
        if old_live:
            return SlotDecision("slot_busy")
        if slot.job_id == job_id:
            # The queue legitimately reclaims this same job after expiry.
            # A new attempt must receive a new fence; the old attempt's
            # token must never be reusable, even with the same job ID.
            previous_claim = _utc(slot.claim_started_at)
            if (
                previous_claim is None
                or type(slot.claim_attempt_count) is not int
                or job.attempt_count <= slot.claim_attempt_count
                or _utc(job.claimed_at) <= previous_claim
            ):
                return SlotDecision("claim_epoch_not_monotonic")
        else:
            # A different job cannot take over an unreconciled predecessor.
            previous = session.get(ProcessingJobModel, slot.job_id)
            if previous is not None and previous.status == "processing":
                return SlotDecision("expired_slot_requires_reconcile")
    if slot.fencing_token >= 9223372036854775807:
        return SlotDecision("fencing_token_exhausted")
    slot.fencing_token += 1
    slot.job_id = job_id
    slot.claim_attempt_count = job.attempt_count
    slot.claim_started_at = _utc(job.claimed_at)
    slot.lease_expires_at = expiry
    slot.updated_at = clock
    session.flush()  # caller commits this with the surrounding job transaction
    return SlotDecision("reserved", slot.fencing_token, expiry)


def renew_image_slot(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, enabled: bool = False,
    emergency_stop: bool = True, ttl_seconds: int = 30,
    now: datetime | None = None,
) -> SlotDecision:
    """A stale job/epoch can never renew an active or successor's lease."""
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return SlotDecision(blocked)
    if emergency_stop is not False:
        return SlotDecision("emergency_stop")
    clock = _utc(now) or datetime.now(timezone.utc)
    job = _claimed_job(
        session, tenant_id=tenant_id, agent_id=agent_id,
        job_id=job_id, now=clock,
    )
    expiry = _expiry(clock, job, ttl_seconds) if job else None
    if expiry is None:
        return SlotDecision("job_claim_not_verified")
    # All mutating APIs lock the source job before device then slot.
    if not _active_image_agent(session, tenant_id, agent_id):
        return SlotDecision("device_not_verified")
    slot = session.get(
        AgentExecutorSlotModel, (tenant_id, agent_id),
        with_for_update=True, populate_existing=True,
    )
    if (
        slot is None or type(fencing_token) is not int
        or fencing_token <= 0
        or slot.fencing_token != fencing_token
        or not _same_claim(slot, job)
    ):
        return SlotDecision("stale_fencing_token")
    if _utc(slot.lease_expires_at) is None or _utc(slot.lease_expires_at) <= clock:
        return SlotDecision("expired_slot_requires_reconcile")
    slot.lease_expires_at = expiry
    slot.updated_at = clock
    session.flush()
    return SlotDecision("renewed", slot.fencing_token, expiry)


def release_finished_image_slot(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, enabled: bool = False,
    now: datetime | None = None,
) -> SlotDecision:
    """Release only a non-processing job with the exact current fence."""
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return SlotDecision(blocked)
    job = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
        ProcessingJobModel.tenant_id == tenant_id,
    ).with_for_update())
    if job is None or job.status == "processing":
        return SlotDecision("job_not_reconciled")
    slot = session.get(
        AgentExecutorSlotModel, (tenant_id, agent_id),
        with_for_update=True, populate_existing=True,
    )
    if (
        slot is None or type(fencing_token) is not int
        or fencing_token <= 0
        or slot.fencing_token != fencing_token
        or not _same_claim(slot, job)
    ):
        return SlotDecision("stale_fencing_token")
    slot.job_id = None
    slot.claim_attempt_count = None
    slot.claim_started_at = None
    slot.lease_expires_at = None
    slot.updated_at = _utc(now) or datetime.now(timezone.utc)
    session.flush()
    return SlotDecision("released", slot.fencing_token)



def verify_image_output_fence(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, enabled: bool = False,
    emergency_stop: bool = True, now: datetime | None = None,
) -> SlotDecision:
    """Check a claim's exact epoch under the job + slot locks, without writes.

    The *future* result importer must call this in its authoritative output
    transaction, preserve both row locks until commit, and reject any failed
    decision BEFORE external side effects. No existing result endpoint calls it.
    This is not an independent browser/provenance attestation.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return SlotDecision(blocked)
    if emergency_stop is not False:
        return SlotDecision("emergency_stop")
    if type(fencing_token) is not int or fencing_token <= 0:
        return SlotDecision("stale_fencing_token")
    clock = _utc(now) or datetime.now(timezone.utc)
    job = _claimed_job(
        session, tenant_id=tenant_id, agent_id=agent_id,
        job_id=job_id, now=clock,
    )
    if job is None:
        return SlotDecision("job_claim_not_verified")
    if not _active_image_agent(session, tenant_id, agent_id):
        return SlotDecision("device_not_verified")
    slot = session.get(
        AgentExecutorSlotModel, (tenant_id, agent_id),
        with_for_update=True, populate_existing=True,
    )
    if (
        slot is None or slot.fencing_token != fencing_token
        or not _same_claim(slot, job)
    ):
        return SlotDecision("stale_fencing_token")
    expires_at = _utc(slot.lease_expires_at)
    if expires_at is None or expires_at <= clock:
        return SlotDecision("expired_slot_requires_reconcile")
    if expires_at > _utc(job.lease_expires_at):
        return SlotDecision("slot_outlives_source_lease")
    return SlotDecision("fence_verified", fencing_token, expires_at)
