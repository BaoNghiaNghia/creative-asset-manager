"""Inert 0209 slot ledger gates: SQLite cannot masquerade as PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.agent_harness.executor_slots import (
    release_finished_image_slot,
    renew_image_slot,
    reserve_existing_image_job,
    verify_image_output_fence,
)
from app.modules.agent_harness.model import AgentExecutorSlotModel
from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel


def test_fail_closed_without_flag_and_without_postgresql(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[
            EtsySpyAgent.__table__,
            ProcessingJobModel.__table__,
            AgentExecutorSlotModel.__table__,
        ],
    )
    with Session(engine) as session:
        kwargs = dict(
            tenant_id="boostgame", agent_id="unused", job_id="unused",
            emergency_stop=False,
        )
        assert reserve_existing_image_job(session, **kwargs).state == "feature_disabled"
        assert reserve_existing_image_job(session, enabled=True, **kwargs).state == "postgresql_required"
        assert renew_image_slot(
            session, enabled=True, fencing_token=1, **kwargs,
        ).state == "postgresql_required"
        assert release_finished_image_slot(
            session, enabled=True, fencing_token=1,
            tenant_id="boostgame", agent_id="unused", job_id="unused",
        ).state == "postgresql_required"
        assert verify_image_output_fence(
            session, enabled=True, fencing_token=1, **kwargs,
        ).state == "postgresql_required"
        assert session.scalar(select(AgentExecutorSlotModel)) is None
    engine.dispose()


def test_migration_0209_empty_and_non_destructive_downgrade(tmp_path):
    url = "sqlite:///" + str(tmp_path / "slots.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.stamp(config, "0208_etsy_spy_agent_review_receipts")
    command.upgrade(config, "0209_cam_agent_executor_slots")
    inspection = inspect(engine)
    assert "cam_agent_executor_slots" in inspection.get_table_names()
    columns = {c["name"] for c in inspection.get_columns("cam_agent_executor_slots")}
    assert {
        "tenant_id", "agent_id", "job_id", "fencing_token",
        "claim_attempt_count", "claim_started_at", "lease_expires_at", "updated_at",
    }.issubset(columns)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM cam_agent_executor_slots")) == 0
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0209_cam_agent_executor_slots"
    command.downgrade(config, "0208_etsy_spy_agent_review_receipts")
    assert "cam_agent_executor_slots" not in inspect(engine).get_table_names()
    engine.dispose()


def test_epoch_identity_is_strict_even_for_same_job_id():
    from app.modules.agent_harness.executor_slots import _same_claim
    clock = datetime(2026, 11, 14, tzinfo=timezone.utc)
    job = ProcessingJobModel(id="job-1", attempt_count=1, claimed_at=clock)
    slot = AgentExecutorSlotModel(
        tenant_id="boostgame", agent_id="agent-1", job_id="job-1",
        claim_attempt_count=1, claim_started_at=clock,
    )
    assert _same_claim(slot, job) is True
    job.attempt_count = 2
    assert _same_claim(slot, job) is False
    job.attempt_count = 1
    job.claimed_at = clock + timedelta(seconds=1)
    assert _same_claim(slot, job) is False


def test_lease_expiry_never_outlives_job():
    from app.modules.agent_harness.executor_slots import _expiry
    clock = datetime(2026, 11, 14, tzinfo=timezone.utc)
    job = ProcessingJobModel(lease_expires_at=clock + timedelta(seconds=12))
    assert _expiry(clock, job, 30) == clock + timedelta(seconds=12)
    assert _expiry(clock, job, 0) is None
    assert _expiry(clock, job, 121) is None
    job.lease_expires_at = clock - timedelta(seconds=1)
    assert _expiry(clock, job, 10) is None
