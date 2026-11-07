"""Verified-source-only Stage 1 vote snapshots; never auto-promote or dispatch."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.registry import AgentDefinition
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router
from app.modules.agent_harness.stage1_feedback import snapshots
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel as Output,
    RrugcKeywordImageJobModel as ImageJob,
)


@pytest.fixture
def fixture(monkeypatch):
    from app.modules.agent_harness import router as module
    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
        AuthAuditEventModel.__table__, Output.__table__, ImageJob.__table__,
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
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=False,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    monkeypatch.setattr(module, "get_settings", lambda: flags)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, principal, flags, module
    db.close()
    engine.dispose()


def data(cohort="music", version="v1", dry_run=True, **overrides):
    payload = {"cohort": cohort, "agent_version": version, "dry_run": dry_run}
    payload.update(overrides)
    return payload


URL = "/api/v1/admin/agent-harness/boostgame/feedback/stage1/candidates"


def setup_feedback_agent(db, tenant="boostgame"):
    repo = AgentHarnessRepository(db)
    repo.register_definition(
        tenant,
        AgentDefinition(
            agent_id="gatorhats.feedback-intelligence", version="v1",
            capabilities=frozenset({"feedback_analyze"}),
            allowed_tools=frozenset({"feedback.read"}),
            preferred_executor="codex", max_concurrent=1, max_retries=0,
        ), actor_id="operator",
    )
    db.commit()


def seed(db, *, tenant="boostgame", phrases=None, mode="safe",
         decision="picked", reason="unspecified", count=4,
         fake_hash=False, qa=True, missing_actor=False):
    phrases = phrases or ("music guitar fun", "rock band tour", "jazz concert today")
    rows = []
    for index, phrase in enumerate(phrases):
        job = ImageJob(
            tenant_id=tenant, keyword_id=f"keyword-{tenant}-{index}",
            keyword_text=phrase, skill_source="codex",
            skill_name="creative-brief-v3", prompt_text="test prompt",
            created_by_user_id="human",
        )
        db.add(job)
        db.flush()
        for slot in range(count):
            digest = sha256(f"{tenant}:{index}:{slot}:artwork".encode()).hexdigest()
            if fake_hash:
                digest = "bad"
            output = Output(
                tenant_id=tenant, stage="stage1", job_id=job.id,
                version=slot + 1, remote_file_id=f"asset-{tenant}-{index}-{slot}",
                content_type="image/png", created_at=datetime.now(timezone.utc),
                font_info_json={
                    "content_sha256": digest,
                    "concept_mode": mode,
                    "concept_variant": "A",
                    "technical_qa_passed": qa,
                    "creative_feedback": {
                        "decision": decision, "reason": reason,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_by": "" if missing_actor else "human",
                    },
                },
            )
            db.add(output)
            rows.append(output)
    db.commit()
    return rows


def counts(db):
    return (
        db.scalar(select(func.count()).select_from(AgentDefinitionModel)),
        db.scalar(select(func.count()).select_from(AgentExperienceModel)),
        db.scalar(select(func.count()).select_from(AuthAuditEventModel)),
    )


def test_preview_requires_flag_and_never_creates_records(fixture):
    client, db, principal, flags, _ = fixture
    seed(db)
    assert client.post(URL, json=data()).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    response = client.post(URL, json=data())
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["dry_run"] is True and item["eligible_patterns"] == 1
    assert item["patterns"][0] == {
        "rule_key": "stage1:pick:safe:A", "sample_count": 12,
        "distinct_keywords": 3, "confidence": 0.25,
        "status": "candidate", "holdout_verified": False,
    }
    assert item["can_promote"] is False
    assert counts(db) == (0, 0, 0)


def test_sync_requires_two_writes_flags_and_registered_inert_agent(fixture):
    client, db, principal, flags, _ = fixture
    seed(db)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.post(URL, json=data(dry_run=False)).status_code == 404
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    assert client.post(URL, json=data(dry_run=False)).status_code == 404
    flags.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED = True
    assert client.post(URL, json=data(dry_run=False)).status_code == 409
    assert counts(db) == (0, 0, 0)
    setup_feedback_agent(db)
    result = client.post(URL, json=data(dry_run=False))
    assert result.status_code == 200, result.text
    assert result.json()["created_candidates"] == 1
    assert counts(db) == (1, 1, 1)
    agent = db.get(AgentDefinitionModel, ("boostgame", "gatorhats.feedback-intelligence", "v1"))
    assert agent.enabled is False
    row = db.scalar(select(AgentExperienceModel))
    assert row.status == "candidate"
    assert row.confidence == 0.25
    assert row.verification_refs_json == []
    assert row.sample_count == len(row.source_event_ids_json) == 12
    assert row.evaluation_json["promotion_allowed"] is False
    assert row.evaluation_json["holdout_verified"] is False
    assert len(row.evidence_refs_json) == 12
    assert all(x.startswith("sha256:") for x in row.evidence_refs_json)
    assert "music" not in str(row.source_event_ids_json)
    audit = db.scalar(select(AuthAuditEventModel))
    assert audit.actor_id == "reviewer" and audit.tenant_id == "boostgame"
    assert audit.action == "agent_stage1_feedback_candidates_synced"

    repeat = client.post(URL, json=data(dry_run=False))
    assert repeat.status_code == 200
    assert repeat.json()["created_candidates"] == 0
    assert counts(db) == (1, 1, 1)


def test_valid_rejections_only_and_no_raw_text_or_prompt_in_evidence(fixture):
    client, db, principal, flags, _ = fixture
    seed(db, decision="rejected", reason="poor_cap_fit")
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    result = client.post(URL, json=data())
    assert result.status_code == 200
    assert result.json()["patterns"][0]["rule_key"] == "stage1:reject:poor_cap_fit"
    assert "music guitar" not in str(result.json())


@pytest.mark.parametrize("options", [
    {"count": 2},  # only 6 votes
    {"phrases": ("music rock", "guitar band")},  # two distinct phrases
    {"fake_hash": True},
    {"qa": False},
    {"missing_actor": True},
    {"decision": "neutral"},
    {"decision": "rejected", "reason": "unspecified"},
    {"phrases": ("dog puppy", "cat pet", "paws kitten")},  # wrong cohort
])
def test_inadequate_unverified_or_neutral_votes_never_make_candidate(fixture, options):
    client, db, principal, flags, _ = fixture
    seed(db, **options)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    result = client.post(URL, json=data())
    assert result.status_code == 200
    assert result.json()["eligible_patterns"] == 0
    assert counts(db) == (0, 0, 0)


def test_vote_undo_does_not_capture_and_old_candidate_stays_inert(fixture):
    client, db, principal, flags, _ = fixture
    outputs = seed(db)
    setup_feedback_agent(db)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    flags.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED = True
    assert client.post(URL, json=data(dry_run=False)).json()["created_candidates"] == 1
    for output in outputs:
        metadata = dict(output.font_info_json)
        metadata["creative_feedback"] = {
            "decision": "neutral", "reason": "unspecified",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "human",
        }
        output.font_info_json = metadata
    db.commit()
    assert client.post(URL, json=data()).json()["eligible_patterns"] == 0
    assert counts(db) == (1, 1, 1)
    assert db.scalar(select(AgentExperienceModel)).status == "candidate"


def test_duplicate_artwork_hash_is_not_independent_evidence(fixture):
    client, db, principal, flags, _ = fixture
    outputs = seed(db)
    common = outputs[0].font_info_json["content_sha256"]
    for output in outputs:
        metadata = dict(output.font_info_json)
        metadata["content_sha256"] = common
        output.font_info_json = metadata
    db.commit()
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.post(URL, json=data()).json()["eligible_patterns"] == 0


def test_tenant_isolation_wrong_version_and_rights_fail(fixture):
    client, db, principal, flags, _ = fixture
    seed(db, tenant="other")
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.post(URL, json=data()).json()["eligible_patterns"] == 0
    principal.active_tenant_id = "other"
    assert client.post(URL, json=data()).status_code == 403
    principal.active_tenant_id = "boostgame"
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.post(URL, json=data()).status_code == 403
    principal.effective_permissions = frozenset({"ai_provider.configure"})
    assert client.post(URL, json=data()).status_code == 403
    client.app.dependency_overrides[require_authenticated_principal] = lambda: (_ for _ in ()).throw(
        HTTPException(status_code=401, detail="Not authenticated")
    )
    assert client.post(URL, json=data()).status_code == 401


@pytest.mark.parametrize("change", [
    {"cohort": "anything"}, {"cohort": "MUSIC"},
    {"agent_version": "latest"}, {"agent_version": "v0"},
    {"source_ids": ["forged-identifier"]}, {"auto_promote": True},
    {"dry_run": "not-a-boolean"},
])
def test_rejects_client_supplied_forged_evidence_or_permissions(fixture, change):
    client, db, principal, flags, _ = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    result = client.post(URL, json=data(**change))
    assert result.status_code == 422, result.text
    assert counts(db) == (0, 0, 0)


def test_emergency_stop_prevents_capture_and_preview(fixture):
    client, db, principal, flags, _ = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AI_EMERGENCY_STOP_ENABLED = True
    seed(db)
    assert client.post(URL, json=data()).status_code == 503


def test_audit_failure_rolls_back_candidate_in_same_transaction(monkeypatch, fixture):
    client, db, principal, flags, module = fixture
    seed(db)
    setup_feedback_agent(db)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    flags.AGENT_HARNESS_FEEDBACK_SYNC_ENABLED = True
    monkeypatch.setattr(module, "AuthAuditEventModel", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("audit fail")))
    result = client.post(URL, json=data(dry_run=False))
    assert result.status_code == 500
    assert counts(db) == (1, 0, 0)
