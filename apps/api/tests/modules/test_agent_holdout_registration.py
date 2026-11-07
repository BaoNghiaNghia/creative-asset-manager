"""Phase 3C.2 prospective study/keyword reservation security and integrity."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.model import (
    AgentDefinitionModel, AgentExperienceModel,
    AgentHoldoutStudyModel, AgentHoldoutAssignmentModel,
)
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router
from app.modules.agent_harness.stage1_feedback import SOURCE_VERSION, snapshots
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel as Keyword,
    RrugcKeywordCreativeBriefModel as Brief,
    RrugcKeywordImageJobModel as ImageJob,
    RrugcImageOutputVersionModel as Output,
)
from tests.modules.test_agent_stage1_feedback_evidence import seed, setup_feedback_agent


BASE = "/api/v1/admin/agent-harness/boostgame/trials/stage1"
STUDY = "holdout-study-001"


@pytest.fixture
def case(monkeypatch):
    from app.modules.agent_harness import router as module

    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        Keyword.__table__, Brief.__table__, ImageJob.__table__, Output.__table__,
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
        AgentHoldoutStudyModel.__table__, AgentHoldoutAssignmentModel.__table__,
        AuthAuditEventModel.__table__,
    ])
    db = Session(engine, expire_on_commit=False)
    principal = SimpleNamespace(
        user_id="reviewer", active_tenant_id="boostgame",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    flags = SimpleNamespace(
        AGENT_HARNESS_READ_API_ENABLED=False,
        AGENT_HARNESS_MANAGEMENT_API_ENABLED=False,
        AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED=False,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    monkeypatch.setattr(module, "get_settings", lambda: flags)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, principal, flags
    db.close()
    engine.dispose()


def _candidate(db, tenant="boostgame"):
    seed(db, tenant=tenant)
    setup_feedback_agent(db, tenant=tenant)
    current = snapshots(db, tenant_id=tenant, cohort="music", agent_version="v1")[0]
    row = AgentHarnessRepository(db).record_candidate(current.candidate)
    row.evaluation_json = {
        "source_kind": SOURCE_VERSION,
        "promotion_allowed": False,
        "holdout_verified": False,
    }
    db.commit()
    return row


def _keyword(db, keyword="music guitar future study", tenant="boostgame"):
    row = Keyword(
        tenant_id=tenant,
        id=str(uuid4()),
        keyword=keyword, keyword_normalized=keyword.casefold(),
        provider="test", search_volume=42, picked=False,
    )
    db.add(row)
    db.commit()
    return row


def _study_payload(candidate, **updates):
    value = {
        "study_id": STUDY, "cohort": "music",
        "candidate_event_key": candidate.event_key,
        "trial_version": "v1", "holdout_percent": 20,
        "reason": "Prospective trial planning only",
        # dry_run True is the default; test explicit write intent.
    }
    value.update(updates)
    return value


def _reserve_payload(keywords, **updates):
    value = {
        "keyword_ids": [item.id for item in keywords],
        "reason": "Pre-register keywords before creative brief generation",
    }
    value.update(updates)
    return value


def _enable(flags):
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    flags.AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED = True


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_registration_default_off_and_dry_run_has_no_side_effects(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    request = _study_payload(candidate)
    assert client.post(BASE, json=request).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.post(BASE, json=request).status_code == 404
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    assert client.post(BASE, json=request).status_code == 404
    _enable(flags)
    before_audits = _count(db, AuthAuditEventModel)
    response = client.post(BASE, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert response.json()["trial_active"] is False
    assert response.json()["generation_enabled"] is False
    assert response.json()["automatic_promotion_enabled"] is False
    assert _count(db, AgentHoldoutStudyModel) == 0
    assert _count(db, AgentHoldoutAssignmentModel) == 0
    assert _count(db, AuthAuditEventModel) == before_audits


def test_persistent_registration_is_immutable_idempotent_and_audited(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    request = _study_payload(candidate, dry_run=False)
    before = _count(db, AuthAuditEventModel)
    response = client.post(BASE, json=request)
    assert response.status_code == 201, response.text
    assert response.json()["created"] is True
    study = db.get(AgentHoldoutStudyModel, ("boostgame", STUDY))
    assert study is not None
    assert study.status == "registered"
    assert len(study.candidate_source_digest) == 64
    assert study.agent_version == "v1"
    assert _count(db, AuthAuditEventModel) == before + 1
    again = client.post(BASE, json=request)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert _count(db, AgentHoldoutStudyModel) == 1
    assert _count(db, AuthAuditEventModel) == before + 1
    changed = client.post(BASE, json=_study_payload(
        candidate, dry_run=False, holdout_percent=30,
    ))
    assert changed.status_code == 409
    db.refresh(study)
    assert study.holdout_percent == 20
    status = client.get(BASE + "/" + STUDY)
    assert status.status_code == 200
    assert status.json()["assignment_counts"] == {"control": 0, "challenger": 0}
    assert status.json()["trial_active"] is False


def test_assignment_reserves_before_generation_and_retries_idempotently(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    keyword = _keyword(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    url = BASE + "/" + STUDY + "/reservations"
    before = _count(db, AuthAuditEventModel)
    dry = client.post(url, json=_reserve_payload([keyword]))
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True
    assert dry.json()["new_assignments"] == 0
    assert _count(db, AgentHoldoutAssignmentModel) == 0
    added = client.post(url, json=_reserve_payload([keyword], dry_run=False))
    assert added.status_code == 200, added.text
    assert added.json()["new_assignments"] == 1
    assert added.json()["trial_active"] is False
    assert added.json()["production_job_routing_enabled"] is False
    assert len(added.json()["reservations"]) == 1
    assert added.json()["reservations"][0]["arm"] in {"control", "challenger"}
    assert _count(db, AuthAuditEventModel) == before + 1
    stored = db.get(AgentHoldoutAssignmentModel, ("boostgame", STUDY, keyword.id))
    assert stored.status == "reserved"
    assert stored.arm == added.json()["reservations"][0]["arm"]
    assert len(stored.keyword_sha256) == 64

    repeat = client.post(url, json=_reserve_payload([keyword], dry_run=False))
    assert repeat.status_code == 200
    assert repeat.json()["new_assignments"] == 0
    assert _count(db, AgentHoldoutAssignmentModel) == 1
    assert _count(db, AuthAuditEventModel) == before + 1
    summary = client.get(BASE + "/" + STUDY).json()
    assert sum(summary["assignment_counts"].values()) == 1
    assert keyword.keyword not in str(summary)
    assert keyword.id not in str(summary)


def test_same_keyword_cannot_be_reserved_in_two_studies(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    keyword = _keyword(db)
    _enable(flags)
    for name in (STUDY, "holdout-study-002"):
        assert client.post(BASE, json=_study_payload(
            candidate, study_id=name, dry_run=False,
        )).status_code == 201
    path1 = BASE + "/" + STUDY + "/reservations"
    path2 = BASE + "/holdout-study-002/reservations"
    req = _reserve_payload([keyword], dry_run=False)
    assert client.post(path1, json=req).status_code == 200
    assert client.post(path2, json=req).status_code == 409
    assert _count(db, AgentHoldoutAssignmentModel) == 1


def test_generated_or_queued_stage1_keyword_is_not_retroactively_reserved(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    keyword = _keyword(db)
    db.add(Brief(
        tenant_id="boostgame", keyword_id=keyword.id,
        keyword_sha256="a" * 64, prompt_version="v1", status="queued",
    ))
    db.commit()
    response = client.post(
        BASE + "/" + STUDY + "/reservations",
        json=_reserve_payload([keyword], dry_run=False),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "keyword_generation_already_registered"
    assert _count(db, AgentHoldoutAssignmentModel) == 0


def test_reservation_rejects_foreign_tenant_or_wrong_cohort(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    outsider = _keyword(db, tenant="other")
    pet = _keyword(db, keyword="dog puppy paws family cat")
    url = BASE + "/" + STUDY + "/reservations"
    assert client.post(url, json=_reserve_payload([outsider], dry_run=False)).status_code == 409
    assert client.post(url, json=_reserve_payload([pet], dry_run=False)).status_code == 409
    assert _count(db, AgentHoldoutAssignmentModel) == 0


def test_changed_keyword_source_fails_replay(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    keyword = _keyword(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    url = BASE + "/" + STUDY + "/reservations"
    request = _reserve_payload([keyword], dry_run=False)
    assert client.post(url, json=request).status_code == 200
    keyword.keyword_normalized = "music guitar changed after reservation"
    db.commit()
    assert client.post(url, json=request).status_code == 409
    assert _count(db, AgentHoldoutAssignmentModel) == 1


def test_undoing_candidate_vote_blocks_study_and_future_reservations(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    assert client.post(BASE, json=_study_payload(candidate, dry_run=False)).status_code == 201
    output = db.scalar(select(Output).where(Output.tenant_id == "boostgame"))
    metadata = dict(output.font_info_json)
    review = dict(metadata["creative_feedback"])
    review["decision"] = "neutral"
    metadata["creative_feedback"] = review
    output.font_info_json = metadata
    db.commit()
    keyword = _keyword(db)
    assert client.post(BASE + "/" + STUDY + "/reservations", json=_reserve_payload(
        [keyword], dry_run=False,
    )).status_code == 409
    assert _count(db, AgentHoldoutAssignmentModel) == 0
    assert client.post(BASE, json=_study_payload(
        candidate, study_id="holdout-study-003", dry_run=False,
    )).status_code == 409


def test_permissions_emergency_stop_and_tenant_boundaries(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    body = _study_payload(candidate, dry_run=False)
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.post(BASE, json=body).status_code == 403
    principal.active_tenant_id = "other"
    principal.effective_permissions = frozenset({"ai_operations.read", "ai_provider.configure"})
    assert client.post(BASE, json=body).status_code == 403
    assert client.get(BASE + "/" + STUDY).status_code == 403
    principal.active_tenant_id = "boostgame"
    flags.AI_EMERGENCY_STOP_ENABLED = True
    assert client.post(BASE, json=body).status_code == 404
    flags.AI_EMERGENCY_STOP_ENABLED = False
    flags.AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED = False
    assert client.post(BASE, json=body).status_code == 404
    assert _count(db, AgentHoldoutStudyModel) == 0


@pytest.mark.parametrize("change", [
    {"trial_active": True}, {"execution_enabled": True},
    {"command": "rm -rf /"}, {"status": "active"}, {"holdout_percent": 100},
    {"holdout_percent": True}, {"trial_version": "latest"},
    {"study_id": "../unsafe"}, {"reason": "bad\nreason"},
    {"reason": "bad"}, {"candidate_event_key": "bad"},
])
def test_unsafe_registration_payload_rejected(case, change):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    result = client.post(BASE, json=_study_payload(
        candidate, dry_run=False, **change,
    ))
    assert result.status_code == 422, result.text
    assert _count(db, AgentHoldoutStudyModel) == 0


@pytest.mark.parametrize("keyword_ids", [
    ["bad"], ["00000000-0000-0000-0000-000000000000"] * 2,
    [123], [], ["../file"],
])
def test_bad_keyword_reservation_payload_is_rejected(case, keyword_ids):
    client, db, principal, flags = case
    _enable(flags)
    response = client.post(BASE + "/" + STUDY + "/reservations", json={
        "keyword_ids": keyword_ids, "reason": "Guard prospective keyword reservations",
        "dry_run": False,
    })
    assert response.status_code == 422
    assert _count(db, AgentHoldoutAssignmentModel) == 0


def test_study_registration_requires_real_source_candidate(case):
    client, db, principal, flags = case
    candidate = _candidate(db)
    _enable(flags)
    body = _study_payload(candidate, dry_run=False, candidate_event_key="f"*64)
    assert client.post(BASE, json=body).status_code == 409
    assert _count(db, AgentHoldoutStudyModel) == 0
