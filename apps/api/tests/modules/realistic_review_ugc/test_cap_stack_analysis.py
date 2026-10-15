"""Gemini Cap Stack image jobs use the canonical RRUGC Gemini pool."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.processing.handlers import ClaimedJob, DeferredJobOutcome, JobOutcome
from app.domain.providers.contracts import AiMetadataAnalysisResult, AiProviderError
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.claim import (
    MODEL_GATED_AI_JOB_TYPES, RRUGC_GEMINI_ANALYZE_JOB_TYPES,
    STARVATION_PREVENTION_EXCLUDED_JOB_TYPES,
)
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.cap_stack_analysis import (
    CAP_STACK_ANALYZE_JOB_TYPE,
    CapStackVisionDocument,
    RrugcCapStackAnalyzeHandler,
    count_status_for,
    enqueue_cap_stack_pending,
)
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    for table in (
        RrugcCapStackImageModel.__table__,
        ProcessingJobModel.__table__,
        TenantProcessingPolicyModel.__table__,
    ):
        table.create(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        engine.dispose()


def seed(db, *, amount=1):
    with db() as session:
        for n in range(amount):
            session.add(RrugcCapStackImageModel(
                id=f"cap-{n}", tenant_id="tenant-a", pin_key=str(100+n),
                pin_url=f"https://www.pinterest.com/pin/{100+n}/",
                image_url=f"https://i.pinimg.com/736x/{100+n}.jpg",
                alt_text="Stack of trucker caps", count_status="pending",
            ))
        session.commit()


def mock_context(db, payload):
    job = ClaimedJob(
        id="processing-one", tenant_id="tenant-a",
        job_type=CAP_STACK_ANALYZE_JOB_TYPE, entity_type="rrugc_cap_stack_image",
        entity_id="cap-0", payload=payload, attempt_count=1,
        lease_owner="image-worker-1", provider_key="gemini",
    )
    return SimpleNamespace(
        job=job, dependencies=SimpleNamespace(session_factory=db),
        logger=logging.LoggerAdapter(logging.getLogger("cap-stack-test"), {}),
        is_cancelled=False,
    )


def sample_document(*, is_stack=True, caps=4, confidence=0.98, suspicious=False):
    return {
        "is_cap_stack": is_stack, "caps_count": caps,
        "count_confidence": confidence, "ai_suspected": suspicious,
        "ai_suspicion_confidence": 0.3,
        "visual_summary": "Several distinct overlapping baseball caps visible on table.",
    }


def test_cap_stack_is_model_gated_and_background_prioritized():
    assert CAP_STACK_ANALYZE_JOB_TYPE in RRUGC_GEMINI_ANALYZE_JOB_TYPES
    assert CAP_STACK_ANALYZE_JOB_TYPE in MODEL_GATED_AI_JOB_TYPES
    assert CAP_STACK_ANALYZE_JOB_TYPE in STARVATION_PREVENTION_EXCLUDED_JOB_TYPES
    assert count_status_for(CapStackVisionDocument(**sample_document())) == "ready"
    assert count_status_for(CapStackVisionDocument(**sample_document(caps=2))) == "ready"
    assert count_status_for(CapStackVisionDocument(**sample_document(caps=6))) == "ready"
    assert count_status_for(CapStackVisionDocument(**sample_document(caps=7))) == "out_of_range"
    assert count_status_for(CapStackVisionDocument(**sample_document(caps=50))) == "out_of_range"
    assert count_status_for(CapStackVisionDocument(**sample_document(caps=1))) == "not_cap_stack"
    assert count_status_for(CapStackVisionDocument(**sample_document(confidence=0.4))) == "needs_review"
    assert count_status_for(CapStackVisionDocument(**sample_document(is_stack=False, caps=1))) == "not_cap_stack"
    with pytest.raises(ValueError):
        CapStackVisionDocument(**sample_document(caps=51))


def test_enqueue_existing_images_bounded_and_idempotent(db):
    seed(db, amount=20)
    with db() as session:
        assert enqueue_cap_stack_pending(session, "tenant-a") == 6
        session.commit()
    with db() as session:
        assert enqueue_cap_stack_pending(session, "tenant-a") == 0
        jobs = list(session.scalars(select(ProcessingJobModel)))
        assert len(jobs) == 6
        assert all(job.provider_key == "gemini" and job.priority == 4 for job in jobs)
        assert all(job.max_attempts == 5 for job in jobs)


def test_success_passes_reserved_model_and_preserves_human_feedback(db, monkeypatch):
    seed(db)
    with db() as session:
        row = session.get(RrugcCapStackImageModel, "cap-0")
        row.manual_caps_count = 6
        row.manual_label = "good"
        session.commit()
    async def fetch(_url):
        return b"test-jpeg", "image/jpeg"
    from app.modules.realistic_review_ugc import cap_stack_analysis as m
    monkeypatch.setattr(m, "fetch_pinterest_image", fetch)
    calls = []
    class Provider:
        async def analyze_single(self, request):
            calls.append(request)
            return AiMetadataAnalysisResult(
                metadata=sample_document(caps=4, suspicious=True),
                provider="gemini", model="gemini-pool-candidate",
            )
    context = mock_context(db, {
        "image_id": "cap-0", "analysis_revision": 1,
        "_ai_model_start_slot": {
            "provider": "gemini", "credential_provider": "gemini_backup_1",
            "model": "gemini-model-from-pool", "worker_id": "image-worker-1",
            "attempt_count": 1,
        },
    })
    context.dependencies.ai_provider_registry = SimpleNamespace(require=lambda name: Provider())
    assert RrugcCapStackAnalyzeHandler()(context).outcome == JobOutcome.COMPLETED
    assert len(calls) == 1
    assert calls[0].preferred_model == "gemini-model-from-pool"
    assert calls[0].preferred_credential_provider == "gemini_backup_1"
    with db() as session:
        row = session.get(RrugcCapStackImageModel, "cap-0")
        assert row.caps_detected == 4
        assert row.count_status == "ready"
        assert row.ai_suspected is True
        assert row.analysis_model == "gemini-pool-candidate"
        assert row.manual_label == "good"
        assert row.manual_caps_count == 6


def test_pool_exhaustion_defers_instead_of_marking_permanent_failure(db, monkeypatch):
    seed(db)
    from app.modules.realistic_review_ugc import cap_stack_analysis as m
    async def fetch(_url):
        return b"jpg", "image/jpeg"
    monkeypatch.setattr(m, "fetch_pinterest_image", fetch)
    class Provider:
        async def analyze_single(self, request):
            error = AiProviderError(
                "Pool is temporarily exhausted",
                code="gemini_model_pool_temporarily_unavailable",
                retryable=True, status_code=429,
            )
            error.earliest_retry_at = datetime.now(timezone.utc) + timedelta(minutes=3)
            raise error
    context = mock_context(db, {"image_id": "cap-0", "analysis_revision": 1})
    context.dependencies.ai_provider_registry = SimpleNamespace(require=lambda name: Provider())
    outcome = RrugcCapStackAnalyzeHandler()(context)
    assert isinstance(outcome, DeferredJobOutcome)
    assert outcome.reason_code == "gemini_model_pool_temporarily_unavailable"
    with db() as session:
        row = session.get(RrugcCapStackImageModel, "cap-0")
        assert row.count_status == "pending"
        assert row.caps_detected is None


def test_stale_retry_never_overwrites_newer_manual_or_gemini_revision(db, monkeypatch):
    seed(db)
    with db() as session:
        row = session.get(RrugcCapStackImageModel, "cap-0")
        row.analysis_revision = 2
        row.manual_label = "ai"
        session.commit()
    context = mock_context(db, {"image_id": "cap-0", "analysis_revision": 1})
    assert RrugcCapStackAnalyzeHandler()(context).outcome == JobOutcome.COMPLETED
    with db() as session:
        row = session.get(RrugcCapStackImageModel, "cap-0")
        assert row.count_status == "pending"
        assert row.manual_label == "ai"