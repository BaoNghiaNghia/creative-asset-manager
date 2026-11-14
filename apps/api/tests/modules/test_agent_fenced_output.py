"""Phase 4C.3D default-OFF helpers and empty migration checks."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.agent_harness.fenced_output import (
    _location, _receipt_identity,
    accept_fenced_image_receipt, prepare_fenced_image_receipt,
)
from app.modules.agent_harness.model import (
    AgentExecutorSlotModel, AgentImageOutputReceiptModel,
)
from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel


def test_fenced_receipts_are_default_off_and_postgres_only(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[
            EtsySpyAgent.__table__, ProcessingJobModel.__table__,
            AgentExecutorSlotModel.__table__,
            AgentImageOutputReceiptModel.__table__,
        ],
    )
    args = dict(
        tenant_id="boostgame", agent_id="agent-id", job_id="job-id",
        fencing_token=1, output_sha256="a" * 64, output_bytes=1024,
        source_sha256="b" * 64, engine="chatgpt_assisted",
    )
    with Session(engine) as session:
        assert prepare_fenced_image_receipt(session, **args).state == "feature_disabled"
        assert prepare_fenced_image_receipt(
            session, **args, enabled=True, emergency_stop=False,
        ).state == "postgresql_required"
        assert accept_fenced_image_receipt(
            session, tenant_id=args["tenant_id"], agent_id=args["agent_id"],
            job_id=args["job_id"], fencing_token=1,
            output_sha256=args["output_sha256"], raw_png=b"x",
            storage_root=tmp_path, source_sha256=args["source_sha256"],
            engine=args["engine"],
        ).state == "feature_disabled"
        assert accept_fenced_image_receipt(
            session, tenant_id=args["tenant_id"], agent_id=args["agent_id"],
            job_id=args["job_id"], fencing_token=1,
            output_sha256=args["output_sha256"], raw_png=b"x",
            storage_root=tmp_path, source_sha256=args["source_sha256"],
            engine=args["engine"],
            enabled=True, emergency_stop=False, provenance_verified=True,
        ).state == "postgresql_required"
        assert session.scalar(select(AgentImageOutputReceiptModel)) is None
    engine.dispose()


def test_empty_0210_migration_and_safe_downgrade(tmp_path):
    db_file = tmp_path / "receipts.sqlite"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    config.attributes["configure_logger"] = False
    engine = create_engine(f"sqlite:///{db_file}")
    command.stamp(config, "0209_cam_agent_executor_slots")
    command.upgrade(config, "0210_cam_agent_image_output_receipts")
    tables = inspect(engine).get_table_names()
    assert "cam_agent_image_output_receipts" in tables
    columns = {col["name"] for col in inspect(engine).get_columns(
        "cam_agent_image_output_receipts",
    )}
    assert {
        "tenant_id", "job_id", "agent_id", "claim_attempt_count",
        "claim_started_at", "fencing_token", "receipt_id",
        "output_sha256", "output_bytes", "source_sha256", "engine",
        "status", "prepared_at", "accepted_at",
    }.issubset(columns)
    with engine.begin() as conn:
        assert conn.scalar(text(
            "SELECT COUNT(*) FROM cam_agent_image_output_receipts",
        )) == 0
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0210_cam_agent_image_output_receipts"
        )
    command.downgrade(config, "0209_cam_agent_executor_slots")
    assert "cam_agent_image_output_receipts" not in inspect(engine).get_table_names()
    engine.dispose()


def test_receipt_identity_is_canonical_and_non_path_controlled():
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    args = dict(
        tenant_id="tenant:with:colons", agent_id="agent",
        job_id="job-id", claim_attempt_count=1,
        claim_started_at=start, fencing_token=1,
        output_sha256=hashlib.sha256(b"safe").hexdigest(),
    )
    original = _receipt_identity(**args)
    assert len(original) == 64
    assert _location(original) == f"fenced-v1/{original[:2]}/{original}.png"
    assert _receipt_identity(**{**args, "claim_attempt_count": 2}) != original
    assert _receipt_identity(**{**args, "fencing_token": 2}) != original
    assert _receipt_identity(**{**args, "agent_id": "other-agent"}) != original
    assert _receipt_identity(**{**args, "tenant_id": "tenant"}) != original
