"""Stage 4 terminal queue reconciliation preserves output and retry safety."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import RrugcStage2GenerateJobHandler
from app.providers.ai.codex_image import CodexImageProviderError
from app.operations import stage4_queue_recovery


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    ProcessingJobModel.__table__.create(engine)
    RrugcStage2JobModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(stage4_queue_recovery, "SessionLocal", factory)
    yield factory
    engine.dispose()


def seed(factory, *, stage_status="queued", processing_status="failed", error="codex_image_output_missing", output=None):
    now = datetime.now(timezone.utc)
    with factory() as session:
        job_id = str(uuid4())
        processing_id = str(uuid4())
        session.add(ProcessingJobModel(
            id=processing_id, tenant_id="test-tenant", job_type="rrugc_stage2_generate",
            entity_type="rrugc_stage2_job", entity_id=job_id, idempotency_key="stage4:"+job_id,
            payload_json={"stage2_job_id": job_id}, status=processing_status,
            max_attempts=3, attempt_count=3, next_attempt_at=now,
            last_error_code=error,
        ))
        session.add(RrugcStage2JobModel(
            id=job_id, tenant_id="test-tenant", source_plan_id=str(uuid4()),
            campaign_id=str(uuid4()), source_revision="v1", skill_name="skill",
            skill_source="local", selected_candidate_ids_json=[], selected_reference_snapshot_json=[],
            status=stage_status, idempotency_key=job_id, processing_job_id=processing_id,
            output_remote_file_id=output, created_by_user_id="actor", created_at=now,
            queued_at=now,
        ))
        session.commit()
    return job_id, processing_id


@pytest.mark.parametrize("count,expected", [(1, False), (3, True)])
def test_worker_marks_last_retryable_failure_terminal(monkeypatch, count, expected):
    async def missing(_self, _context):
        raise CodexImageProviderError("codex_image_output_missing", "No generated image", retryable=True)

    seen = []
    monkeypatch.setattr(RrugcStage2GenerateJobHandler, "_execute", missing)
    monkeypatch.setattr(
        RrugcStage2GenerateJobHandler, "_mark_failure",
        lambda _self, _context, _code, _message, **kwargs: seen.append(kwargs.get("terminal")),
    )
    context = SimpleNamespace(
        job=SimpleNamespace(attempt_count=count, max_attempts=3, entity_id="row",
                            tenant_id="test-tenant", payload={"stage2_job_id": "row"}),
        logger=logging.getLogger("stage4-test"),
    )
    RrugcStage2GenerateJobHandler()(context)
    assert seen == [expected]


def test_recovery_requeues_once_without_overwriting_completed_jobs(db, monkeypatch):
    orphan, old = seed(db)
    completed, _ = seed(db, stage_status="completed", processing_status="completed", output="published")
    runs = []

    def retry(self, *, tenant_id, user_id, job_id):
        assert tenant_id == "test-tenant" and user_id == "actor"
        runs.append(job_id)
        row = self.session.get(RrugcStage2JobModel, job_id)
        row.status = "queued"
        new = ProcessingJobModel(
            tenant_id=tenant_id, job_type="rrugc_stage2_generate", entity_type="rrugc_stage2_job",
            entity_id=job_id, idempotency_key="replay:"+job_id,
            payload_json={"stage2_job_id": job_id}, status="pending", max_attempts=3,
            next_attempt_at=datetime.now(timezone.utc),
        )
        self.session.add(new)
        self.session.flush()
        row.processing_job_id = new.id
        self.session.commit()

    monkeypatch.setattr(stage4_queue_recovery.RrugcStage2Service, "retry_failed_job", retry)
    result = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert result == {"eligible": 1, "failed_synced": 1, "auto_requeued": 1, "skipped": 0}
    assert runs == [orphan]
    with db() as session:
        saved = session.get(RrugcStage2JobModel, completed)
        assert saved.status == "completed" and saved.output_remote_file_id == "published"
        resumed = session.get(RrugcStage2JobModel, orphan)
        assert resumed.status == "queued" and resumed.processing_job_id != old
        processing = session.get(ProcessingJobModel, resumed.processing_job_id)
        assert processing.status == "pending"
        # A second terminal failure must be visible, not requeued infinitely.
        processing.status = "failed"
        processing.attempt_count = 3
        processing.last_error_code = "codex_image_output_missing"
        session.commit()
    result2 = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert result2["failed_synced"] == 1 and result2["auto_requeued"] == 0
    with db() as session:
        assert session.get(RrugcStage2JobModel, orphan).status == "failed"
        assert session.query(ProcessingJobModel).filter_by(entity_id=orphan).count() == 2


def test_recovery_dry_run_and_unrelated_error_never_requeue(db, monkeypatch):
    orphan, old = seed(db, error="stage2_source_changed")
    monkeypatch.setattr(
        stage4_queue_recovery.RrugcStage2Service, "retry_failed_job",
        lambda *_a, **_kw: pytest.fail("Non-imagegen failure must not replay"),
    )
    dry = stage4_queue_recovery.reconcile_stage4_terminal(limit=5, dry_run=True)
    assert dry["eligible"] == 1 and dry["failed_synced"] == 0
    with db() as session:
        assert session.get(RrugcStage2JobModel, orphan).status == "queued"
    changed = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert changed["failed_synced"] == 1 and changed["auto_requeued"] == 0
    with db() as session:
        assert session.get(RrugcStage2JobModel, orphan).last_error_code == "stage2_source_changed"
        assert session.get(ProcessingJobModel, old).status == "failed"
