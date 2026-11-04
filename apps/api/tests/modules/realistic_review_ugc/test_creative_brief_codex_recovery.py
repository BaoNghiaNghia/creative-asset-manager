"""Codex quota failures are durable, resumable and never require paid API keys."""
import asyncio
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.creative_brief import (
    CodexBriefUnavailable, _codex_unavailable, generate_brief,
)
from app.modules.realistic_review_ugc.creative_brief_service import (
    CODEX_THROTTLE_FILE, JOB_TYPE, PROMPT_VERSION,
    CreativeBriefGenerateHandler, get_brief, brief_summary,
)
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
    RrugcKeywordImageJobModel, RrugcImageOutputVersionModel,
)
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerResult


@pytest.mark.parametrize(("message", "reason"), [
    ("ERROR: You've hit your usage limit. Try again on Monday.",
     "creative_brief_codex_quota_wait"),
    ("rate_limit_exceeded (HTTP 429)", "creative_brief_codex_quota_wait"),
    ("ERROR: Login required for Codex", "creative_brief_codex_auth_wait"),
    ("Network error: connection reset by peer", "creative_brief_codex_network_wait"),
])
def test_classifies_recoverable_codex_failure_without_leaking_diagnostics(message, reason):
    failure = _codex_unavailable(message.encode(), b"")
    assert isinstance(failure, CodexBriefUnavailable)
    assert failure.reason_code == reason
    assert message not in str(failure)
    assert _codex_unavailable(b"Invalid model name", b"") is None


def test_brief_cli_classifies_exhausted_quota_and_does_not_use_paid_key(monkeypatch):
    observed = {}

    class FakeProcess:
        returncode = 1
        async def wait(self):
            return 1
        def kill(self):
            raise AssertionError("no kill expected")

    async def fake_exec(*args, **kwargs):
        observed["environment"] = kwargs["env"]
        kwargs["stderr"].write(b"ERROR: You've hit your usage limit")
        kwargs["stdout"].write(b"")
        return FakeProcess()

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use-paid-api")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(CodexBriefUnavailable) as result:
        asyncio.run(generate_brief("SUPPORT LIVE MUSIC", timeout_seconds=10))
    assert result.value.reason_code == "creative_brief_codex_quota_wait"
    assert "OPENAI_API_KEY" not in observed["environment"]


def test_worker_quota_defers_all_queued_briefs_and_resumes_once_available(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    RrugcKeywordImageJobModel.__table__.create(engine)
    RrugcImageOutputVersionModel.__table__.create(engine)
    ProcessingJobModel.__table__.create(engine)

    def make_brief(session, quote, state):
        keyword_id, brief_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
        session.add(RrugcKeywordVolumeModel(
            id=keyword_id, tenant_id="tenant-a", keyword=quote,
            keyword_normalized=quote.lower(), picked=True, search_volume=0,
        ))
        session.add(RrugcKeywordCreativeBriefModel(
            id=brief_id, tenant_id="tenant-a", keyword_id=keyword_id,
            keyword_sha256=hashlib.sha256(quote.encode()).hexdigest(),
            prompt_version=PROMPT_VERSION, status="queued", processing_job_id=job_id,
        ))
        session.add(ProcessingJobModel(
            id=job_id, tenant_id="tenant-a", job_type=JOB_TYPE,
            entity_type="rrugc_keyword_creative_brief", entity_id=brief_id,
            idempotency_key="brief-" + job_id, payload_json={},
            status=state, max_attempts=2, attempt_count=int(state == "processing"),
        ))
        return keyword_id, brief_id, job_id

    with Session(engine) as session:
        keyword_a, brief_a, job_a = make_brief(session, "HELLO CODEx", "processing")
        keyword_b, brief_b, job_b = make_brief(session, "SECOND PHRASE", "pending")
        session.commit()

    def context(brief_id, job_id):
        return SimpleNamespace(
            is_cancelled=False,
            job=SimpleNamespace(
                id=job_id, tenant_id="tenant-a", entity_id=brief_id,
            ),
            dependencies=SimpleNamespace(session_factory=lambda: Session(engine)),
        )

    calls = []
    async def fake_brief(phrase, **kwargs):
        calls.append(phrase)
        if len(calls) == 1:
            raise CodexBriefUnavailable("creative_brief_codex_quota_wait", 1800)
        return SimpleNamespace(model_dump=lambda mode: {
            "keyword_exact": phrase, "concepts": [{"mode": "safe"}],
        })

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_brief_service.generate_brief",
        fake_brief,
    )
    settings = SimpleNamespace(
        RRUGC_CREATIVE_BRIEF_ENABLED=True,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
        RRUGC_CREATIVE_BRIEF_MAX_CONCURRENCY=2,
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_HOME="",
        RRUGC_CREATIVE_BRIEF_MODEL="",
        RRUGC_CREATIVE_BRIEF_TIMEOUT_SECONDS=30,
        RRUGC_CREATIVE_BRIEF_AUTO_RETRY_ENABLED=True,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_brief_service.get_settings",
        lambda: settings,
    )
    handler = CreativeBriefGenerateHandler(settings)
    first = handler(context(brief_a, job_a))
    assert isinstance(first, DeferredJobOutcome)
    assert first.reason_code == "creative_brief_codex_quota_wait"
    assert first.retry_at > datetime.now(timezone.utc)
    with Session(engine) as session:
        assert session.get(RrugcKeywordCreativeBriefModel, brief_a).status == "queued"
        row_b = session.get(RrugcKeywordCreativeBriefModel, brief_b)
        assert row_b.last_error_code == "creative_brief_codex_quota_wait"
        job = session.get(ProcessingJobModel, job_b)
        assert job.status == "pending" and job.attempt_count == 0
        assert job.next_attempt_at.replace(tzinfo=timezone.utc) >= first.retry_at.replace(microsecond=0)
        detail = get_brief(session, tenant_id="tenant-a", keyword_id=keyword_b)
        assert detail["status"] == "queued"
        assert detail["error_code"] == "creative_brief_codex_quota_wait"
        assert detail["next_retry_at"] is not None
        assert brief_summary(row_b)["error_code"] == "creative_brief_codex_quota_wait"

    second = handler(context(brief_b, job_b))
    assert isinstance(second, DeferredJobOutcome)
    assert second.reason_code == first.reason_code
    assert calls == ["HELLO CODEx"]  # No new Codex usage while quota is exhausted.

    # Once the account is usable again the same job continues; nothing was
    # terminalized and no new ProcessingJob or keyword row was created.
    (tmp_path / CODEX_THROTTLE_FILE).write_text("0 creative_brief_codex_quota_wait\n")
    recovered = handler(context(brief_b, job_b))
    assert recovered == JobHandlerResult.completed()
    assert calls == ["HELLO CODEx", "SECOND PHRASE"]
    with Session(engine) as session:
        row = session.get(RrugcKeywordCreativeBriefModel, brief_b)
        assert row.status == "completed" and row.last_error_code is None
        assert session.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.id == job_b,
        )) is not None
    engine.dispose()
