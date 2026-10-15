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


@pytest.mark.parametrize("count", [1, 3])
def test_worker_defers_codex_failure_even_after_max_attempts(db, monkeypatch, count):
    from app.domain.processing.handlers import ClaimedJob

    async def missing(_self, _context):
        raise CodexImageProviderError("codex_image_output_missing", "No generated image", retryable=True)

    monkeypatch.setattr(RrugcStage2GenerateJobHandler, "_execute", missing)
    job_id, processing_id = seed(db, stage_status="running", processing_status="processing")
    with db() as session:
        processing = session.get(ProcessingJobModel, processing_id)
        processing.attempt_count = count
        session.commit()
    # A real ClaimedJob has NO max_attempts member. Regression-test the
    # production envelope instead of adding a field to a fake context.
    context = SimpleNamespace(
        job=ClaimedJob(
            id=processing_id, attempt_count=count,
            job_type="rrugc_stage2_generate", tenant_id="test-tenant",
            entity_type="rrugc_stage2_job", entity_id=job_id,
            payload={"stage2_job_id": job_id}, lease_owner="test-worker",
        ),
        dependencies=SimpleNamespace(session_factory=db),
        logger=logging.getLogger("stage4-test"),
    )
    from app.domain.processing.handlers import DeferredJobOutcome
    outcome = RrugcStage2GenerateJobHandler()(context)
    assert isinstance(outcome, DeferredJobOutcome)
    assert outcome.reason_code == "codex_image_output_missing"
    with db() as session:
        assert session.get(RrugcStage2JobModel, job_id).status == "queued"


def test_recovery_reuses_processing_job_and_preserves_completed_output(db):
    orphan, previous = seed(db)
    completed, _ = seed(db, stage_status="completed", processing_status="completed", output="published")

    result = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert result == {"eligible": 1, "failed_synced": 0, "auto_requeued": 1, "skipped": 0}
    with db() as session:
        assert session.get(RrugcStage2JobModel, completed).output_remote_file_id == "published"
        resumed = session.get(RrugcStage2JobModel, orphan)
        assert resumed.status == "queued"
        assert resumed.processing_job_id == previous
        processing = session.get(ProcessingJobModel, previous)
        assert processing.status == "pending"
        assert processing.attempt_count == 0
        processing.status = "failed"
        processing.attempt_count = 3
        session.commit()

    # Even repeated outages cannot exhaust the recovery budget or grow history.
    again = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert again["auto_requeued"] == 1
    with db() as session:
        assert session.query(ProcessingJobModel).filter_by(entity_id=orphan).count() == 1
        assert session.get(RrugcStage2JobModel, orphan).status == "queued"


def test_only_proven_legacy_attribute_failure_is_replayed_once(db, monkeypatch):
    from datetime import timedelta

    job_id, original_id = seed(db)
    with db() as session:
        row = session.get(RrugcStage2JobModel, job_id)
        first = session.get(ProcessingJobModel, original_id)
        first.created_at = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
        second = ProcessingJobModel(
            tenant_id="test-tenant", job_type="rrugc_stage2_generate",
            entity_type="rrugc_stage2_job", entity_id=job_id,
            idempotency_key="legacy-attribute:"+job_id,
            payload_json={"stage2_job_id": job_id}, status="failed",
            attempt_count=3, max_attempts=3, next_attempt_at=datetime.now(timezone.utc),
            last_error_code="AttributeError",
            created_at=datetime(2026, 10, 12, 8, tzinfo=timezone.utc),
        )
        session.add(second)
        session.flush()
        row.processing_job_id = second.id
        row.status = "failed"
        row.last_error_code = "AttributeError"
        session.commit()

    replayed = []
    def retry(self, *, tenant_id, user_id, job_id):
        replayed.append(job_id)
        self.session.add(ProcessingJobModel(
            tenant_id=tenant_id, job_type="rrugc_stage2_generate",
            entity_type="rrugc_stage2_job", entity_id=job_id,
            idempotency_key="third-and-last:"+job_id,
            payload_json={"stage2_job_id": job_id}, status="pending",
            next_attempt_at=datetime.now(timezone.utc),
        ))
        row = self.session.get(RrugcStage2JobModel, job_id)
        row.status = "queued"
        self.session.commit()

    monkeypatch.setattr(stage4_queue_recovery.RrugcStage2Service, "retry_failed_job", retry)
    dry = stage4_queue_recovery.replay_legacy_attribute_error(dry_run=True)
    assert dry["eligible"] == 1 and dry["requeued"] == 0 and not replayed
    value = stage4_queue_recovery.replay_legacy_attribute_error()
    assert value == {"eligible": 1, "requeued": 1, "blocked": 0}
    assert replayed == [job_id]
    assert stage4_queue_recovery.replay_legacy_attribute_error()["requeued"] == 0


def test_legacy_regression_replay_rejects_non_matching_error_chain(db, monkeypatch):
    job_id, old = seed(db, error="stage2_source_changed")
    with db() as session:
        row = session.get(RrugcStage2JobModel, job_id)
        row.last_error_code = "AttributeError"
        row.status = "failed"
        session.commit()
    monkeypatch.setattr(stage4_queue_recovery.RrugcStage2Service, "retry_failed_job",
                        lambda *_a, **_kw: pytest.fail("Unproven history must not replay"))
    assert stage4_queue_recovery.replay_legacy_attribute_error()["eligible"] == 0

def test_recovery_dry_run_then_revives_all_failed_processing_codes(db):
    orphan, previous = seed(db, error="stage2_source_changed")
    dry = stage4_queue_recovery.reconcile_stage4_terminal(limit=5, dry_run=True)
    assert dry["eligible"] == 1 and dry["auto_requeued"] == 0
    with db() as session:
        assert session.get(ProcessingJobModel, previous).status == "failed"
    changed = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert changed["auto_requeued"] == 1
    with db() as session:
        assert session.get(RrugcStage2JobModel, orphan).status == "queued"
        assert session.get(ProcessingJobModel, previous).status == "pending"


def test_previously_failed_transient_job_is_requeued(db, monkeypatch):
    job_id, _ = seed(db, stage_status="failed", error="managed_storage_unavailable")
    with db() as session:
        row = session.get(RrugcStage2JobModel, job_id)
        row.last_error_code = "managed_storage_unavailable"
        session.commit()

    def retry(self, *, tenant_id, user_id, job_id):
        row = self.session.get(RrugcStage2JobModel, job_id)
        row.status = "queued"
        self.session.commit()

    monkeypatch.setattr(stage4_queue_recovery.RrugcStage2Service, "retry_failed_job", retry)
    result = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert result["auto_requeued"] == 1
    with db() as session:
        assert session.get(RrugcStage2JobModel, job_id).status == "queued"


def test_exhausted_failed_job_returns_to_queue(db, monkeypatch):
    job_id, _ = seed(db, stage_status="failed")
    with db() as session:
        row = session.get(RrugcStage2JobModel, job_id)
        row.last_error_code = "codex_image_output_missing"
        for index in range(3):
            session.add(ProcessingJobModel(
                tenant_id="test-tenant", job_type="rrugc_stage2_generate",
                entity_type="rrugc_stage2_job", entity_id=job_id,
                idempotency_key=f"exhausted:{index}:{job_id}", payload_json={"stage2_job_id": job_id},
                status="failed", max_attempts=3, attempt_count=3,
                last_error_code="codex_image_output_missing",
                next_attempt_at=datetime.now(timezone.utc),
            ))
        session.commit()
    result = stage4_queue_recovery.reconcile_stage4_terminal(limit=5)
    assert result["auto_requeued"] == 1
    with db() as session:
        row = session.get(RrugcStage2JobModel, job_id)
        assert row.status == "queued"
        processing = session.get(ProcessingJobModel, row.processing_job_id)
        assert processing.status == "pending"
        assert processing.attempt_count == 0
