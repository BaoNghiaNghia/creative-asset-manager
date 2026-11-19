"""Real PostgreSQL 12/16 concurrency and epoch fencing (disposable CI DB only)."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.agent_harness.executor_slots import (
    release_finished_image_slot,
    renew_image_slot,
    reserve_existing_image_job,
    verify_image_output_fence,
)
from app.modules.agent_harness.model import AgentExecutorSlotModel
from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel


@pytest.fixture
def ledger():
    url = os.environ.get("INTEGRATION_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("requires explicitly provisioned disposable PostgreSQL integration DB")
    engine = create_engine(url, pool_pre_ping=True, pool_size=4, max_overflow=4)
    if engine.dialect.name != "postgresql":
        pytest.skip("transactional row-lock test requires PostgreSQL")
    tenant_id = "fence-" + uuid4().hex
    agent_id = str(uuid4())
    job_id = str(uuid4())
    clock = datetime.now(timezone.utc).replace(microsecond=123456)
    with Session(engine) as session:
        session.add(EtsySpyAgent(
            id=agent_id, tenant_id=tenant_id,
            name="CAM Image Agent / Fencing CI", token_hash="a" * 64,
            active=True,
        ))
        session.add(ProcessingJobModel(
            id=job_id, tenant_id=tenant_id, job_type="etsy_design_extract",
            entity_type="etsy_spy_listing", entity_id=uuid4().hex,
            idempotency_key="fencing-" + uuid4().hex, payload_json={},
            status="processing", attempt_count=1, max_attempts=5,
            claimed_by="etsy-image:" + agent_id, claimed_at=clock,
            lease_expires_at=clock + timedelta(minutes=2),
        ))
        session.commit()
    yield engine, tenant_id, agent_id, job_id, clock
    with Session(engine) as session:
        session.execute(
            delete(AgentExecutorSlotModel).where(AgentExecutorSlotModel.tenant_id == tenant_id)
        )
        session.execute(
            delete(ProcessingJobModel).where(ProcessingJobModel.tenant_id == tenant_id)
        )
        session.execute(delete(EtsySpyAgent).where(EtsySpyAgent.tenant_id == tenant_id))
        session.commit()
    engine.dispose()


def reserve(session: Session, tenant: str, agent: str, job: str, clock: datetime):
    return reserve_existing_image_job(
        session, tenant_id=tenant, agent_id=agent, job_id=job,
        enabled=True, emergency_stop=False, now=clock,
    )


def test_concurrent_identical_claims_are_serialized_and_idempotent(ledger):
    engine, tenant, agent, job, clock = ledger
    barrier = threading.Barrier(2)

    def worker():
        with Session(engine) as session:
            barrier.wait(timeout=15)
            decision = reserve(session, tenant, agent, job, clock)
            session.commit()
            return decision

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        decisions = [first.result(timeout=30), second.result(timeout=30)]

    assert {x.state for x in decisions} == {"reserved", "already_reserved"}
    assert {x.fencing_token for x in decisions} == {1}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AgentExecutorSlotModel).where(
            AgentExecutorSlotModel.tenant_id == tenant,
        )) == 1
        slot = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert slot.claim_attempt_count == 1
        assert slot.claim_started_at == clock


def test_reclaimed_job_same_id_gets_new_fence_and_blocks_old_writes(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        first = reserve(session, tenant, agent, job, clock)
        assert first.state == "reserved" and first.fencing_token == 1
        session.commit()

    with Session(engine) as session:
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
            emergency_stop=False, now=clock + timedelta(seconds=5),
        ).state == "fence_verified"
        session.rollback()

    # Simulate a forced/recovered claim while the old executor slot is still
    # live. This tests fail-closed admission, not the normal lease-expiry path.
    new_clock = clock + timedelta(seconds=20)
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.attempt_count = 2
        source.claimed_at = new_clock
        source.lease_expires_at = clock + timedelta(seconds=125)
        session.commit()

    with Session(engine) as session:
        assert reserve(
            session, tenant, agent, job, new_clock,
        ).state == "slot_busy"
        session.rollback()
    reclaim_ready = clock + timedelta(seconds=31)
    with Session(engine) as session:
        second = reserve(session, tenant, agent, job, reclaim_ready)
        assert second.state == "reserved" and second.fencing_token == 2
        session.commit()

    with Session(engine) as session:
        assert renew_image_slot(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
            emergency_stop=False, now=reclaim_ready,
        ).state == "stale_fencing_token"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
            emergency_stop=False, now=reclaim_ready,
        ).state == "stale_fencing_token"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=second.fencing_token, enabled=True,
            emergency_stop=False, now=reclaim_ready,
        ).state == "fence_verified"
        session.rollback()

    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.status = "completed"
        session.commit()
    with Session(engine) as session:
        assert release_finished_image_slot(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
        ).state == "stale_fencing_token"
        assert release_finished_image_slot(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=second.fencing_token, enabled=True,
        ).state == "released"
        session.commit()
    with Session(engine) as session:
        slot = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert slot.fencing_token == 2
        assert slot.job_id is None
        assert slot.claim_attempt_count is None
        assert slot.claim_started_at is None
        assert slot.lease_expires_at is None


def test_expired_same_attempt_needs_reconciliation_and_concurrent_job_denied(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    with Session(engine) as session:
        assert reserve(
            session, tenant, agent, job, clock + timedelta(seconds=31),
        ).state == "expired_slot_requires_reconcile"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=31),
        ).state == "expired_slot_requires_reconcile"
        session.rollback()

    second_job_id = str(uuid4())
    with Session(engine) as session:
        session.add(ProcessingJobModel(
            id=second_job_id, tenant_id=tenant, job_type="etsy_design_extract",
            entity_type="etsy_spy_listing", entity_id=uuid4().hex,
            idempotency_key="fencing-" + uuid4().hex, payload_json={},
            status="processing", attempt_count=1, max_attempts=5,
            claimed_by="etsy-image:" + agent, claimed_at=clock,
            lease_expires_at=clock + timedelta(minutes=2),
        ))
        session.commit()
    with Session(engine) as session:
        assert reserve(session, tenant, agent, second_job_id, clock).state == "unresolved_device_claim"
        session.rollback()
        assert reserve(session, tenant, agent, job, clock).state == "unresolved_device_claim"
        session.rollback()
    with Session(engine) as session:
        slot = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert slot.fencing_token == 1


def test_another_agent_cannot_take_a_job_with_an_unreconciled_slot(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        initial = reserve(session, tenant, agent, job, clock)
        assert initial.state == "reserved" and initial.fencing_token == 1
        session.commit()

    second_agent = str(uuid4())
    handoff_time = clock + timedelta(seconds=45)  # prior slot expired at +30
    with Session(engine) as session:
        session.add(EtsySpyAgent(
            id=second_agent, tenant_id=tenant,
            name="CAM Image Agent / Handoff CI", token_hash="b" * 64,
            active=True,
        ))
        source = session.get(ProcessingJobModel, job)
        source.claimed_by = "etsy-image:" + second_agent
        source.attempt_count = 2
        source.claimed_at = handoff_time
        source.lease_expires_at = handoff_time + timedelta(minutes=2)
        session.commit()

    with Session(engine) as session:
        decision = reserve(session, tenant, second_agent, job, handoff_time)
        assert decision.state == "job_reserved_elsewhere"
        session.rollback()
    with Session(engine) as session:
        assert session.get(AgentExecutorSlotModel, (tenant, second_agent)) is None
        original = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert original.job_id == job
        assert original.fencing_token == 1
        assert original.claim_attempt_count == 1
        assert session.get(ProcessingJobModel, job).claimed_by == "etsy-image:" + second_agent


def test_reclaim_rejects_missing_monotonic_claim_epoch(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()

    retry_time = clock + timedelta(seconds=41)  # slot TTL ended at +30
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.claimed_at = clock + timedelta(seconds=40)
        source.lease_expires_at = retry_time + timedelta(minutes=2)
        session.commit()

    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, retry_time).state == "claim_epoch_not_monotonic"
        session.rollback()
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.attempt_count = 2
        source.claimed_at = clock  # attempt increased, but claim time did not
        session.commit()
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, retry_time).state == "claim_epoch_not_monotonic"
        session.rollback()
    with Session(engine) as session:
        slot = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert slot.fencing_token == 1
        assert slot.claim_attempt_count == 1


def test_shortened_source_lease_must_be_refenced_before_output(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()

    shorter_expiry = clock + timedelta(seconds=15)
    with Session(engine) as session:
        session.get(ProcessingJobModel, job).lease_expires_at = shorter_expiry
        session.commit()

    with Session(engine) as session:
        assert reserve(
            session, tenant, agent, job, clock + timedelta(seconds=5),
        ).state == "slot_outlives_source_lease"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=5),
        ).state == "slot_outlives_source_lease"
        session.rollback()
    with Session(engine) as session:
        shortened = renew_image_slot(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=6),
        )
        assert shortened.state == "renewed"
        assert shortened.expires_at == shorter_expiry
        session.commit()
    with Session(engine) as session:
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=7),
        ).state == "fence_verified"
        session.rollback()


def test_cross_tenant_and_emergency_stop_fail_closed(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        assert reserve(
            session, tenant + "-other", agent, job, clock,
        ).state == "job_claim_not_verified"
        assert reserve_existing_image_job(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            enabled=True, emergency_stop=True, now=clock,
        ).state == "emergency_stop"
        assert reserve_existing_image_job(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            enabled=False, emergency_stop=False, now=clock,
        ).state == "feature_disabled"
        session.rollback()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AgentExecutorSlotModel)) == 0


def test_revoked_device_cannot_reserve_or_validate_output(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    with Session(engine) as session:
        source = session.get(EtsySpyAgent, agent)
        source.active = False
        session.commit()
    with Session(engine) as session:
        assert reserve(session, tenant, agent, job, clock).state == "device_not_verified"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, enabled=True, emergency_stop=False, now=clock,
        ).state == "device_not_verified"
        session.rollback()


def test_postgres_constraint_rejects_missing_claim_epoch(ledger):
    engine, tenant, agent, job, clock = ledger
    with Session(engine) as session:
        session.add(AgentExecutorSlotModel(
            tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=1, lease_expires_at=clock + timedelta(seconds=20),
            claim_attempt_count=None, claim_started_at=None,
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AgentExecutorSlotModel).where(
            AgentExecutorSlotModel.tenant_id == tenant,
        )) == 0
