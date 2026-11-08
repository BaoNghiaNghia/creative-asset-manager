"""Phase 3C.3A: first-queue shadow admission is never a treatment exposure."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.agent_harness.model import AgentHoldoutAssignmentModel
from app.modules.agent_harness.trial_admission import TrialAdmissionConflict
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.creative_brief_service import queue_brief
from app.modules.realistic_review_ugc.model import RrugcKeywordCreativeBriefModel
from tests.modules.test_agent_holdout_registration import (
    BASE, STUDY, _candidate, _enable, _keyword, _reserve_payload,
    _study_payload, case,
)


@pytest.fixture
def prepared(case, monkeypatch):
    client, db, principal, flags = case
    ProcessingJobModel.__table__.create(db.bind)
    candidate = _candidate(db)
    keyword = _keyword(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    assert client.post(
        BASE + "/" + STUDY + "/reservations",
        json=_reserve_payload([keyword], dry_run=False),
    ).status_code == 200
    keyword.picked = True
    db.commit()

    def fake_create_job(repo, **kwargs):
        job = ProcessingJobModel(
            id=str(uuid4()), tenant_id=kwargs["tenant_id"],
            job_type=kwargs["job_type"], entity_type=kwargs["entity_type"],
            entity_id=kwargs["entity_id"],
            idempotency_key=kwargs["idempotency_key"],
            payload_json=dict(kwargs["payload"]),
            priority=kwargs["priority"], status="pending", max_attempts=1,
        )
        repo.session.add(job)
        repo.session.flush()
        return job

    monkeypatch.setattr(ProcessingRepository, "create_job", fake_create_job)
    return client, db, flags, keyword, candidate


def _settings(flags, **overrides):
    data = {
        **vars(flags),
        "RRUGC_CREATIVE_BRIEF_ENABLED": True,
        "RRUGC_CREATIVE_BRIEF_AUTO_RETRY_ENABLED": False,
        "AGENT_HARNESS_TRIAL_ADMISSION_ENABLED": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_opt_out_queue_has_no_shadow_payload_or_audit(prepared):
    _, db, flags, keyword, _ = prepared
    before = _count(db, AuthAuditEventModel)
    assert queue_brief(
        db, tenant_id="boostgame", keyword_id=keyword.id,
        settings=_settings(flags, AGENT_HARNESS_TRIAL_ADMISSION_ENABLED=False),
    ) == ("queued", True)
    db.commit()
    task = db.scalar(select(ProcessingJobModel))
    assert "trial_shadow_admission" not in task.payload_json
    assert _count(db, AuthAuditEventModel) == before


def test_admission_freezes_shadow_manifest_but_not_exposure(prepared):
    _, db, flags, keyword, _ = prepared
    before = _count(db, AuthAuditEventModel)
    settings = _settings(flags)
    assert queue_brief(
        db, tenant_id="boostgame", keyword_id=keyword.id, settings=settings,
        actor_id="reviewer",
    ) == ("queued", True)
    db.commit()
    task = db.scalar(select(ProcessingJobModel))
    payload = task.payload_json["trial_shadow_admission"]
    assert payload["study_id"] == STUDY
    assert payload["arm"] in ("control", "challenger")
    assert payload["mode"] == "shadow_only"
    assert payload["treatment_applied"] is False
    assert payload["exposure_verified"] is False
    assert payload["independent_qa_verified"] is False
    assignment = db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id))
    assert assignment.status == "reserved"
    assert _count(db, AuthAuditEventModel) == before + 1
    assert queue_brief(
        db, tenant_id="boostgame", keyword_id=keyword.id, settings=settings,
        actor_id="reviewer",
    ) == ("queued", False)
    db.commit()
    assert _count(db, AuthAuditEventModel) == before + 1
    assert _count(db, ProcessingJobModel) == 1


@pytest.mark.parametrize("mutation", ["changed_keyword", "unregistered", "wrong_arm", "stale_candidate"])
def test_invalid_reservations_never_queue_or_emit_audit(prepared, monkeypatch, mutation):
    _, db, flags, keyword, candidate = prepared
    from app.modules.agent_harness.model import AgentHoldoutStudyModel
    from app.modules.agent_harness import trial_admission
    assignment = db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id))
    study = db.get(AgentHoldoutStudyModel, ("boostgame", STUDY))
    if mutation == "changed_keyword":
        keyword.keyword_normalized = "different normalized keyword"
    elif mutation == "unregistered":
        study.status = "cancelled"
    elif mutation == "wrong_arm":
        assignment.arm = "control" if assignment.arm == "challenger" else "challenger"
    else:
        monkeypatch.setattr(trial_admission, "_agent_source_current", lambda *a, **k: False)
    db.commit()
    before = _count(db, AuthAuditEventModel)
    with pytest.raises(TrialAdmissionConflict):
        queue_brief(db, tenant_id="boostgame", keyword_id=keyword.id, settings=_settings(flags))
    db.rollback()
    assert _count(db, ProcessingJobModel) == 0
    assert _count(db, RrugcKeywordCreativeBriefModel) == 0
    assert _count(db, AuthAuditEventModel) == before


def test_preexisting_brief_does_not_become_shadow_exposure(prepared):
    _, db, flags, keyword, _ = prepared
    db.add(RrugcKeywordCreativeBriefModel(
        tenant_id="boostgame", keyword_id=keyword.id,
        keyword_sha256="a" * 64, prompt_version="old", status="failed",
    ))
    db.commit()
    with pytest.raises(TrialAdmissionConflict, match="trial_keyword_not_prospective"):
        queue_brief(db, tenant_id="boostgame", keyword_id=keyword.id, settings=_settings(flags))
    db.rollback()
    assert _count(db, ProcessingJobModel) == 0
