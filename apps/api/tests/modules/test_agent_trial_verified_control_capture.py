"""3C.3B2A: only a proven successful baseline worker may write a control receipt.

SQLite fixtures test auth/provenance relationships; production migration
compatibility was already validated against PostgreSQL at 0205. No provider
calls, no challenger treatment, and no QA verdict are made in these tests.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import logging
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobOutcome, WorkerDependencies
from app.modules.agent_harness.holdout import assign_keyword_arm
from app.modules.agent_harness.model import (
    AgentHoldoutAssignmentModel, AgentTrialExposureModel, AgentTrialQaReviewModel,
)
from app.modules.agent_harness.trial_exposure import (
    TrialExposureConflict, control_capture_enabled,
    record_verified_control_exposure,
)
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.creative_brief import build_prompt
from app.modules.realistic_review_ugc.creative_brief_service import (
    CreativeBriefGenerateHandler, PROMPT_VERSION, queue_brief,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordCreativeBriefModel
from tests.modules.test_agent_holdout_registration import (
    BASE, STUDY, _candidate, _enable, _keyword,
    _reserve_payload, _study_payload, case,
)
from tests.modules.test_agent_trial_shadow_admission import _settings

WORKER = "test-worker-001"


def _digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _claim(task) -> ClaimedJob:
    return ClaimedJob(
        id=task.id, tenant_id=task.tenant_id, job_type=task.job_type,
        entity_type=task.entity_type, entity_id=task.entity_id,
        payload=dict(task.payload_json), attempt_count=task.attempt_count,
        lease_owner=WORKER,
    )


def _setup(case, monkeypatch, *, arm: str = "control"):
    client, db, principal, flags = case
    ProcessingJobModel.__table__.create(db.bind)
    AgentTrialExposureModel.__table__.create(db.bind)
    AgentTrialQaReviewModel.__table__.create(db.bind)
    candidate = _candidate(db)
    keyword = _keyword(db)
    # Keyword-level assignment must use the real algorithm. Choose a UUID
    # deterministically by scanning candidates; never forge arm metadata.
    for _ in range(200):
        if assign_keyword_arm(
            tenant_id="boostgame", cohort="music",
            study_version=f"v1.{STUDY}", keyword_id=keyword.id,
            holdout_percent=20,
        ) == arm:
            break
        keyword.id = str(uuid4())
    else:
        raise AssertionError("Could not select a deterministic arm")
    db.commit()
    _enable(flags)
    assert client.post(
        BASE, json=_study_payload(candidate, dry_run=False),
    ).status_code == 201
    assert client.post(
        BASE + "/" + STUDY + "/reservations",
        json=_reserve_payload([keyword], dry_run=False),
    ).status_code == 200
    keyword.picked = True
    db.commit()

    def fake_create_job(repo, **kwargs):
        task = ProcessingJobModel(
            id=str(uuid4()), tenant_id=kwargs["tenant_id"],
            job_type=kwargs["job_type"], entity_type=kwargs["entity_type"],
            entity_id=kwargs["entity_id"], idempotency_key=kwargs["idempotency_key"],
            payload_json=dict(kwargs["payload"]), priority=kwargs["priority"],
            status="pending", max_attempts=1,
        )
        repo.session.add(task)
        repo.session.flush()
        return task

    monkeypatch.setattr(ProcessingRepository, "create_job", fake_create_job)
    settings = _settings(flags, AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED=True)
    assert queue_brief(
        db, tenant_id="boostgame", keyword_id=keyword.id, settings=settings,
        actor_id="operator",
    ) == ("queued", True)
    db.commit()
    task = db.scalar(select(ProcessingJobModel))
    brief = db.scalar(select(RrugcKeywordCreativeBriefModel))
    task.status = "processing"
    task.claimed_by = WORKER
    brief.status = "completed"
    brief.brief_json = {"keyword_exact": keyword.keyword}
    db.commit()
    assert db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id)).arm == arm
    return db, task, brief, keyword, settings


def _record(db, task, keyword, settings, **kwargs):
    values = {
        "job": _claim(task),
        "keyword_id": keyword.id, "policy_version": PROMPT_VERSION,
        "applied_prompt_sha256": _digest(build_prompt(keyword.keyword)),
        "settings": settings,
    }
    values.update(kwargs)
    return record_verified_control_exposure(db, **values)


def test_control_receipt_is_once_only_and_audited_in_same_transaction(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    before = _count(db, AuthAuditEventModel)
    assert _record(db, task, keyword, settings)
    db.commit()
    assert _count(db, AgentTrialExposureModel) == 1
    row = db.get(AgentTrialExposureModel, ("boostgame", STUDY, keyword.id))
    assert row.arm == "control"
    assert row.worker_identity == WORKER
    assert row.processing_job_id == task.id
    assert row.policy_sha256 == _digest(build_prompt(keyword.keyword))
    assert db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id)).status == "exposed"
    assert _count(db, AuthAuditEventModel) == before + 1
    assert not _record(db, task, keyword, settings)
    db.commit()
    assert _count(db, AgentTrialExposureModel) == 1
    assert _count(db, AuthAuditEventModel) == before + 1


def test_challenger_baseline_is_never_exposed_or_promoted(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch, arm="challenger")
    before = _count(db, AuthAuditEventModel)
    assert not _record(db, task, keyword, settings)
    db.commit()
    assert _count(db, AgentTrialExposureModel) == 0
    assert db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id)).status == "reserved"
    assert _count(db, AuthAuditEventModel) == before


def test_feature_off_and_unadmitted_jobs_have_no_exposure(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    assert not control_capture_enabled(_settings(
        SimpleNamespace(**vars(settings)),
        AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED=False,
    ))
    disabled = SimpleNamespace(**{
        **vars(settings), "AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED": False,
    })
    assert not _record(db, task, keyword, disabled)
    assert _count(db, AgentTrialExposureModel) == 0
    task.payload_json = {
        k: v for k, v in task.payload_json.items() if k != "trial_shadow_admission"
    }
    db.commit()
    assert not _record(db, task, keyword, settings)
    db.commit()
    assert _count(db, AgentTrialExposureModel) == 0


@pytest.mark.parametrize("invalid", [
    "other_tenant", "wrong_job_owner", "changed_keyword_digest",
    "forged_shadow_claim", "other_claim_payload", "study_cancelled",
    "wrong_job_status", "missing_brief",
])
def test_invalid_or_cross_tenant_receipts_fail_closed(case, monkeypatch, invalid):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    values = {}
    if invalid == "other_tenant":
        values["job"] = replace(_claim(task), tenant_id="other")
    elif invalid == "wrong_job_owner":
        task.claimed_by = "different-worker"
    elif invalid == "changed_keyword_digest":
        keyword.keyword_normalized = "different keyword"
    elif invalid == "forged_shadow_claim":
        task.payload_json = {
            **task.payload_json,
            "trial_shadow_admission": {
                **task.payload_json["trial_shadow_admission"], "exposure_verified": True,
            },
        }
        values["job"] = _claim(task)
    elif invalid == "other_claim_payload":
        values["job"] = replace(_claim(task), payload={"keyword_id": keyword.id})
    elif invalid == "study_cancelled":
        from app.modules.agent_harness.model import AgentHoldoutStudyModel
        db.get(AgentHoldoutStudyModel, ("boostgame", STUDY)).status = "cancelled"
    elif invalid == "wrong_job_status":
        task.status = "pending"
    elif invalid == "missing_brief":
        brief.brief_json = None
    db.commit()
    with pytest.raises(TrialExposureConflict):
        _record(db, task, keyword, settings, **values)
    db.rollback()
    assert _count(db, AgentTrialExposureModel) == 0
    assert db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id)).status == "reserved"


def test_read_only_evidence_status_tracks_receipts_not_quality_claims(case, monkeypatch):
    client, db, principal, flags = case
    url = BASE + "/" + STUDY + "/evidence"
    assert client.get(url).status_code == 404  # read flag default OFF
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    before = _count(db, AuthAuditEventModel)
    initial = client.get(url)
    assert initial.status_code == 200, initial.text
    assert initial.json()["recorded_exposure_receipts"] == {"control": 0, "challenger": 0}
    assert initial.json()["assignment_counts"]["control"]["reserved"] == 1
    assert initial.json()["independent_qa_verified"] is False
    assert initial.json()["evaluation_status"] == "blocked"
    assert "challenger_policy_not_applied" in initial.json()["evaluation_blockers"]
    assert keyword.id not in initial.text and keyword.keyword not in initial.text
    assert _count(db, AuthAuditEventModel) == before

    assert _record(db, task, keyword, settings)
    db.commit()
    current = client.get(url)
    assert current.status_code == 200, current.text
    assert current.json()["recorded_exposure_receipts"] == {"control": 1, "challenger": 0}
    assert current.json()["assignment_counts"]["control"]["exposed"] == 1
    assert current.json()["qa_rows_unverified"] == 0
    assert current.json()["trial_active"] is False
    assert current.json()["automatic_promotion_enabled"] is False
    assert keyword.id not in current.text
    assert client.get(url.replace("boostgame", "other-tenant")).status_code == 403


def test_reissued_digest_cannot_change_immutable_receipt(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    assert _record(db, task, keyword, settings)
    db.commit()
    with pytest.raises(TrialExposureConflict, match="immutable"):
        _record(db, task, keyword, settings, applied_prompt_sha256="f" * 64)
    db.rollback()
    assert _count(db, AgentTrialExposureModel) == 1


def test_completed_worker_path_records_control_only_after_codex_success(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    brief.status = "queued"
    brief.brief_json = None
    db.commit()
    settings.CODEX_IMAGE_BINARY = "codex"
    settings.CODEX_IMAGE_HOME = ""
    settings.RRUGC_CREATIVE_BRIEF_MODEL = ""
    settings.RRUGC_CREATIVE_BRIEF_TIMEOUT_SECONDS = 20
    settings.TYPOGRAPHY_V2_ENABLED = False

    from app.modules.realistic_review_ugc import (
        stage1_design_learning, keyword_cohort_learning,
    )
    monkeypatch.setattr(stage1_design_learning, "learning_profile", lambda *a, **k: None)
    monkeypatch.setattr(stage1_design_learning, "learning_prompt_context", lambda *_: "")
    monkeypatch.setattr(keyword_cohort_learning, "cross_keyword_learning", lambda *a, **k: None)
    monkeypatch.setattr(keyword_cohort_learning, "cross_keyword_guidance", lambda *_: "")

    calls = []
    async def fake_generate(phrase, **kwargs):
        calls.append((phrase, kwargs["learning_context"]))
        return SimpleNamespace(model_dump=lambda **_k: {"keyword_exact": phrase})

    from app.modules.realistic_review_ugc import creative_brief_service
    monkeypatch.setattr(creative_brief_service, "generate_brief", fake_generate)
    context = JobHandlerContext(
        job=_claim(task),
        dependencies=WorkerDependencies(session_factory=lambda: Session(
            db.bind, expire_on_commit=False,
        )),
        shutdown_requested=Event(), cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
    )
    result = CreativeBriefGenerateHandler(settings)._execute_locked(context)
    assert result.outcome == JobOutcome.COMPLETED
    assert calls == [(keyword.keyword, "")]
    db.expire_all()
    assert _count(db, AgentTrialExposureModel) == 1
    assert db.scalar(select(RrugcKeywordCreativeBriefModel)).status == "completed"


def test_failed_codex_execution_never_writes_exposure(case, monkeypatch):
    db, task, brief, keyword, settings = _setup(case, monkeypatch)
    brief.status = "queued"
    brief.brief_json = None
    db.commit()
    settings.CODEX_IMAGE_BINARY = "codex"
    settings.CODEX_IMAGE_HOME = ""
    settings.RRUGC_CREATIVE_BRIEF_MODEL = ""
    settings.RRUGC_CREATIVE_BRIEF_TIMEOUT_SECONDS = 20
    settings.TYPOGRAPHY_V2_ENABLED = False

    from app.modules.realistic_review_ugc import (
        stage1_design_learning, keyword_cohort_learning, creative_brief_service,
    )
    monkeypatch.setattr(stage1_design_learning, "learning_profile", lambda *a, **k: None)
    monkeypatch.setattr(stage1_design_learning, "learning_prompt_context", lambda *_: "")
    monkeypatch.setattr(keyword_cohort_learning, "cross_keyword_learning", lambda *a, **k: None)
    monkeypatch.setattr(keyword_cohort_learning, "cross_keyword_guidance", lambda *_: "")

    async def broken_generate(*_args, **_kwargs):
        raise RuntimeError("test simulated codex failure")

    monkeypatch.setattr(creative_brief_service, "generate_brief", broken_generate)
    context = JobHandlerContext(
        job=_claim(task),
        dependencies=WorkerDependencies(session_factory=lambda: Session(
            db.bind, expire_on_commit=False,
        )),
        shutdown_requested=Event(), cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
    )
    result = CreativeBriefGenerateHandler(settings)._execute_locked(context)
    assert result.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    db.expire_all()
    assert _count(db, AgentTrialExposureModel) == 0
    assert db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id)).status == "reserved"
    assert db.scalar(select(RrugcKeywordCreativeBriefModel)).status == "failed"
