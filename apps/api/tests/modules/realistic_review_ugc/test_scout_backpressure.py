"""Global Scout discovery brake when the tenant's Gemini analysis lane is backlogged."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.modules.realistic_review_ugc.scout_automation as scout_automation
from app.modules.processing.model import ProcessingJobModel


def _job(*, tenant: str, index: int, created_at: datetime) -> ProcessingJobModel:
    return ProcessingJobModel(
        tenant_id=tenant,
        job_type="rrugc_candidate_analyze",
        entity_type="rrugc_candidate",
        entity_id=f"candidate-{index}",
        idempotency_key=f"analyze:{tenant}:{index}",
        status="pending",
        priority=0,
        created_at=created_at,
        next_attempt_at=created_at,
        payload_json={},
        provider_key="gemini",
    )


def test_scout_pauses_only_on_old_tenant_backlog(monkeypatch):
    monkeypatch.setattr(scout_automation, "SCOUT_ANALYSIS_BACKLOG_LIMIT", 3)
    monkeypatch.setattr(scout_automation, "SCOUT_ANALYSIS_BACKLOG_MIN_AGE_SECONDS", 900)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProcessingJobModel.__table__.create(engine)
    now = datetime(2026, 10, 7, 3, 30, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            session.add_all([
                _job(tenant="tenant-a", index=i, created_at=now - timedelta(minutes=18))
                for i in range(3)
            ])
            session.add(_job(tenant="tenant-b", index=10, created_at=now - timedelta(days=1)))
            session.commit()
            pressure = scout_automation.scout_analysis_backpressure(
                session, "tenant-a", now=now,
            )
            assert pressure["active"] is True
            assert pressure["pending_jobs"] == 3
            assert pressure["oldest_wait_seconds"] >= 1080
            assert scout_automation.scout_analysis_backpressure(
                session, "tenant-b", now=now,
            )["active"] is False
            assert scout_automation.scout_analysis_backpressure(
                session, "tenant-a", now=now - timedelta(minutes=12),
            )["active"] is False
    finally:
        engine.dispose()


def test_recovered_backlog_resumes_claims(monkeypatch):
    monkeypatch.setattr(scout_automation, "SCOUT_ANALYSIS_BACKLOG_LIMIT", 2)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProcessingJobModel.__table__.create(engine)
    now = datetime(2026, 10, 7, 3, 30, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            session.add_all([
                _job(tenant="tenant-a", index=i, created_at=now - timedelta(hours=1))
                for i in range(2)
            ])
            session.commit()
            assert scout_automation.scout_analysis_backpressure(
                session, "tenant-a", now=now,
            )["active"] is True
            session.query(ProcessingJobModel).filter(
                ProcessingJobModel.entity_id == "candidate-0"
            ).update({"status": "completed"})
            session.commit()
            assert scout_automation.scout_analysis_backpressure(
                session, "tenant-a", now=now,
            )["active"] is False
    finally:
        engine.dispose()
