"""Agent Harness tenant isolation, default-off API and idempotent storage."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.agent_harness.experience import ExperienceCandidate
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.registry import AgentDefinition
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def agent(version="v1", **overrides):
    args = dict(
        agent_id="gatorhats.typography", version=version,
        capabilities=frozenset({"image_edit"}),
        allowed_tools=frozenset({"design.read", "design.write"}),
        preferred_executor="codex", fallback_executors=("browser",),
        max_concurrent=2, max_retries=1,
    )
    args.update(overrides)
    return AgentDefinition(**args)


def candidate(tenant="boostgame", **overrides):
    args = dict(
        tenant_id=tenant, agent_id="gatorhats.typography",
        agent_version="v1", scope_key="heritage",
        rule_key="layout-serif", confidence=.92, sample_count=12,
        event_ids=tuple(f"vote-{i}" for i in range(12)),
        evidence_refs=("asset-hash-001",), verification_refs=("holdout-001",),
    )
    args.update(overrides)
    return ExperienceCandidate(**args)


def test_registry_persists_disabled_version_and_tenant_isolates(db):
    repo = AgentHarnessRepository(db)
    stored = repo.register_definition("boostgame", agent(), actor_id="reviewer")
    assert stored.enabled is False
    assert repo.register_definition("boostgame", agent(), actor_id="reviewer").version == "v1"
    with pytest.raises(ValueError, match="immutable"):
        repo.register_definition("boostgame", agent(max_retries=2), actor_id="reviewer")
    repo.register_definition("other", agent(), actor_id="other-reviewer")
    db.commit()
    assert len(repo.list_definitions("boostgame")) == 1
    assert len(repo.list_definitions("other")) == 1
    assert repo.get_definition("missing", "gatorhats.typography", "v1") is None
    assert repo.get_definition("boostgame", "gatorhats.typography", "v2") is None


def test_experiences_idempotent_and_no_cross_tenant_promotion(db):
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", agent(), actor_id="reviewer")
    repo.register_definition("other", agent(), actor_id="reviewer")
    first = repo.record_candidate(candidate())
    assert first.status == "candidate"
    same = repo.record_candidate(candidate())
    assert same.event_key == first.event_key
    with pytest.raises(ValueError, match="idempotency"):
        repo.record_candidate(candidate(confidence=.81))
    other = repo.record_candidate(candidate("other"))
    assert first.event_key != other.event_key
    db.commit()
    assert repo.experience_counts("boostgame") == {
        "candidate": 1, "promoted": 0, "rejected": 0,
    }
    assert repo.experience_counts("other") == {
        "candidate": 1, "promoted": 0, "rejected": 0,
    }
    assert repo.eligible_experiences("boostgame", "gatorhats.typography", "v1", "heritage") == []
    assert repo.eligible_experiences("other", "gatorhats.typography", "v1", "heritage") == []


def test_experience_must_reference_registered_tenant_agent(db):
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", agent(), actor_id="reviewer")
    with pytest.raises(ValueError, match="not registered"):
        repo.record_candidate(candidate("other"))
    with pytest.raises(ValueError, match="not registered"):
        repo.record_candidate(candidate(agent_version="v2"))
    with pytest.raises(ValueError, match="bounds"):
        repo.record_candidate(candidate(event_ids=()))


def test_read_api_defaults_off_and_checks_permission_and_tenant(monkeypatch, db):
    from app.modules.agent_harness import router as module

    app = FastAPI()
    app.include_router(router)
    principal = SimpleNamespace(
        active_tenant_id="boostgame",
        effective_permissions=frozenset({"ai_operations.read"}),
        platform_admin=False,
    )
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(AGENT_HARNESS_READ_API_ENABLED=False))
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", agent(), actor_id="reviewer")
    repo.register_definition("other", agent(), actor_id="reviewer")

    with TestClient(app) as client:
        url = "/api/v1/admin/agent-harness/boostgame/overview"
        assert client.get(url).status_code == 404
        monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(AGENT_HARNESS_READ_API_ENABLED=True))
        ok = client.get(url)
        assert ok.status_code == 200
        assert ok.json()["execution_enabled"] is False
        assert ok.json()["agent_count"] == 1
        assert ok.json()["agents"][0]["enabled"] is False
        assert client.get("/api/v1/admin/agent-harness/other/overview").status_code == 403
        principal.effective_permissions = frozenset()
        assert client.get(url).status_code == 403
