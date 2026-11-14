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

    # A second claim of the SAME job_id is an entirely new lease epoch.
    new_clock = clock + timedelta(seconds=35)
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.attempt_count = 2
        source.claimed_at = new_clock
        source.lease_expires_at = clock + timedelta(seconds=125)
        session.commit()

    with Session(engine) as session:
        assert reserve(
            session, tenant, agent, job, clock + timedelta(seconds=10),
        ).state == "slot_busy"
        session.rollback()
    with Session(engine) as session:
        second = reserve(session, tenant, agent, job, new_clock)
        assert second.state == "reserved" and second.fencing_token == 2
        session.commit()

    with Session(engine) as session:
        assert renew_image_slot(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
            emergency_stop=False, now=new_clock,
        ).state == "stale_fencing_token"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=first.fencing_token, enabled=True,
            emergency_stop=False, now=new_clock,
        ).state == "stale_fencing_token"
        assert verify_image_output_fence(
            session, tenant_id=tenant, agent_id=agent, job_id=job,
            fencing_token=second.fencing_token, enabled=True,
            emergency_stop=False, now=new_clock,
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
