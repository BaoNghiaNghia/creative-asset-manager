"""Phase 3B.2: read-only Stage 1 evidence audit and tenant/RBAC regression."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router
from app.modules.agent_harness.stage1_feedback import SOURCE_VERSION, snapshots
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordImageJobModel as ImageJob,
    RrugcImageOutputVersionModel as Output,
)
from tests.modules.test_agent_stage1_feedback_evidence import seed, setup_feedback_agent


URL = "/api/v1/admin/agent-harness/boostgame/feedback/stage1/evidence-audit?cohort=music"


@pytest.fixture
def scenario(monkeypatch):
    from app.modules.agent_harness import router as module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
                ImageJob.__table__, Output.__table__],
    )
    session = Session(engine, expire_on_commit=False)
    principal = SimpleNamespace(
        user_id="reviewer", active_tenant_id="boostgame",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    flags = SimpleNamespace(AGENT_HARNESS_READ_API_ENABLED=False)
    monkeypatch.setattr(module, "get_settings", lambda: flags)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, session, principal, flags
    session.close()
    engine.dispose()


def register_current(db, *, tenant="boostgame"):
    setup_feedback_agent(db, tenant=tenant)
    summary = snapshots(db, tenant_id=tenant, cohort="music", agent_version="v1")[0]
    row = AgentHarnessRepository(db).record_candidate(summary.candidate)
    row.evaluation_json = {
        "source_kind": SOURCE_VERSION, "promotion_allowed": False,
        "independent_qa_passed": False, "holdout_verified": False,
    }
    db.commit()
    return row


def test_audit_read_api_is_default_off_and_permissions_fail_closed(scenario):
    client, db, principal, flags = scenario
    seed(db)
    register_current(db)
    assert client.get(URL).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.get(URL).status_code == 200
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.get(URL).status_code == 403
    principal.effective_permissions = frozenset({"ai_provider.configure"})
    assert client.get(URL).status_code == 403
    principal.effective_permissions = frozenset({"ai_operations.read", "ai_provider.configure"})
    assert client.get(URL.replace("boostgame", "another-tenant")).status_code == 403
    client.app.dependency_overrides[require_authenticated_principal] = lambda: (
        _ for _ in ()).throw(HTTPException(status_code=401, detail="Unauthenticated"))
    assert client.get(URL).status_code == 401


def test_current_evidence_is_not_trusted_qa_or_promoted(scenario):
    client, db, principal, flags = scenario
    seed(db)
    row = register_current(db)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    original_count = db.scalar(select(func.count()).select_from(AgentExperienceModel))

    response = client.get(URL)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["result_count"] == 1
    assert report["audit_mode"] == "read_only"
    assert not report["automatic_promotion_enabled"]
    assert not report["agent_execution_enabled"]
    entry = report["items"][0]
    assert entry["event_key"] == row.event_key
    assert entry["rule_key"] == "stage1:pick:safe:A"
    assert entry["review_status"] == "current_candidate"
    assert entry["source_current"] is True
    assert entry["sample_count"] == 12
    assert entry["distinct_keywords"] == 3
    assert entry["promotion_allowed"] is False
    assert entry["independent_qa_verified"] is False
    assert entry["holdout_verified"] is False
    assert "music guitar" not in response.text
    assert "asset-boostgame" not in response.text
    assert "stage1-output:" not in response.text
    assert "sha256:" not in response.text
    assert db.scalar(select(func.count()).select_from(AgentExperienceModel)) == original_count
    db.refresh(row)
    assert row.status == "candidate"


def test_undo_invalidates_source_without_writing_database(scenario):
    client, db, principal, flags = scenario
    outputs = seed(db)
    row = register_current(db)
    original_key = row.event_key
    selected = outputs[0]
    metadata = dict(selected.font_info_json)
    review = dict(metadata["creative_feedback"])
    review["decision"] = "neutral"
    metadata["creative_feedback"] = review
    selected.font_info_json = metadata
    db.commit()

    flags.AGENT_HARNESS_READ_API_ENABLED = True
    report = client.get(URL).json()
    assert report["items"][0]["review_status"] == "stale_source"
    assert report["items"][0]["source_current"] is False
    assert report["items"][0]["promotion_allowed"] is False
    db.refresh(row)
    assert row.status == "candidate"
    assert row.event_key == original_key


def test_untrusted_metadata_and_unsafe_statuses_cannot_appear_verified(scenario):
    client, db, principal, flags = scenario
    seed(db)
    row = register_current(db)
    flags.AGENT_HARNESS_READ_API_ENABLED = True

    row.evaluation_json = {"source_kind": "arbitrary", "holdout_verified": True}
    db.commit()
    report = client.get(URL).json()
    assert report["items"][0]["review_status"] == "unsupported_source"
    assert report["items"][0]["holdout_verified"] is False

    row.evaluation_json = {"source_kind": SOURCE_VERSION}
    row.verification_refs_json = ["forged-proof"]
    db.commit()
    report = client.get(URL).json()
    assert report["items"][0]["review_status"] == "unexpected_verification"
    assert report["items"][0]["promotion_allowed"] is False

    row.status = "promoted"
    db.commit()
    report = client.get(URL).json()
    assert report["items"][0]["review_status"] == "promoted_requires_review"
    assert report["items"][0]["promotion_allowed"] is False


def test_invalid_query_never_reads_unbounded_cohorts(scenario):
    client, db, principal, flags = scenario
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.get(URL.replace("cohort=music", "cohort=arbitrary")).status_code == 422
    assert client.get(URL + "&limit=31").status_code == 422
    assert client.get(URL + "&limit=0").status_code == 422
    assert client.get(URL + "&limit=-1").status_code == 422
    assert client.get(URL.replace("?cohort=music", "")).status_code == 422
    assert client.get(URL).json()["result_count"] == 0


def test_cross_tenant_items_do_not_appear_in_boostgame(scenario):
    client, db, principal, flags = scenario
    seed(db, tenant="other")
    register_current(db, tenant="other")
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    report = client.get(URL).json()
    assert report["result_count"] == 0
    assert report["status_counts"]["current_candidate"] == 0
