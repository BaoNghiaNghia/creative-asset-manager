"""Phase 2C: permission/tenant/audit/immutability/inert config HTTP regression."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.router import router
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import require_authenticated_principal


def registration(**changes) -> dict:
    payload = {
        "agent_id": "gatorhats.typography",
        "version": "v1",
        "capabilities": ["generate_text", "image_edit"],
        "allowed_tools": ["brief.read", "design.read", "design.write"],
        "preferred_executor": "codex",
        "fallback_executors": ["browser"],
        "max_concurrent": 2,
        "max_retries": 1,
        "reason": "Register heritage typography for QA only",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def fixture(monkeypatch):
    from app.modules.agent_harness import router as module

    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
        AuthAuditEventModel.__table__,
    ])
    db = Session(engine, expire_on_commit=False)
    principal = SimpleNamespace(
        user_id="user-a", active_tenant_id="boostgame",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: db
    flags = SimpleNamespace(
        AGENT_HARNESS_READ_API_ENABLED=False,
        AGENT_HARNESS_MANAGEMENT_API_ENABLED=False,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
    )
    monkeypatch.setattr(module, "get_settings", lambda: flags)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, principal, flags, module
    db.close()
    engine.dispose()


def counts(db) -> tuple[int, int]:
    return (
        db.scalar(select(func.count()).select_from(AgentDefinitionModel)),
        db.scalar(select(func.count()).select_from(AuthAuditEventModel)),
    )


def test_management_is_not_exposed_by_default(fixture):
    client, db, principal, flags, _ = fixture
    path = "/api/v1/admin/agent-harness/boostgame/agents"
    assert client.post(path, json=registration()).status_code == 404
    assert counts(db) == (0, 0)
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    assert client.post(path, json=registration()).status_code == 404
    assert counts(db) == (0, 0)
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = False
    assert client.post(path, json=registration()).status_code == 404
    assert counts(db) == (0, 0)


def test_registration_is_disabled_idempotent_and_atomically_audited(fixture):
    client, db, principal, flags, _ = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    url = "/api/v1/admin/agent-harness/boostgame/agents"
    result = client.post(url, json=registration())
    assert result.status_code == 201, result.text
    assert result.json() == {
        "tenant_id": "boostgame", "agent_id": "gatorhats.typography",
        "version": "v1", "enabled": False, "created": True,
        "execution_enabled": False,
    }
    row = db.get(AgentDefinitionModel, ("boostgame", "gatorhats.typography", "v1"))
    assert row.enabled is False
    assert row.created_by == "user-a"
    assert row.allowed_tools_json == ["brief.read", "design.read", "design.write"]
    audit = db.scalar(select(AuthAuditEventModel))
    assert audit.tenant_id == "boostgame"
    assert audit.actor_id == "user-a"
    assert audit.action == "agent_catalog_registered"
    assert audit.detail_json["version"] == "v1"
    assert audit.detail_json["reason"] == "Register heritage typography for QA only"
    assert "secret" not in str(audit.detail_json).lower()
    assert counts(db) == (1, 1)

    result = client.post(url, json=registration())
    assert result.status_code == 200
    assert result.json()["created"] is False
    assert counts(db) == (1, 1)

    result = client.post(url, json=registration(max_retries=3))
    assert result.status_code == 409
    assert counts(db) == (1, 1)
    assert db.get(AgentDefinitionModel, ("boostgame", "gatorhats.typography", "v1")).max_retries == 1

    created_v2 = client.post(url, json=registration(version="v2"))
    assert created_v2.status_code == 201
    assert counts(db) == (2, 2)
    overview = client.get("/api/v1/admin/agent-harness/boostgame/overview")
    assert overview.status_code == 200
    assert overview.json()["agent_count"] == 2
    assert overview.json()["execution_enabled"] is False


def test_denies_cross_tenant_and_unprivileged_role(fixture):
    client, db, principal, flags, _ = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    path = "/api/v1/admin/agent-harness/boostgame/agents"

    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.post(path, json=registration()).status_code == 403
    principal.effective_permissions = frozenset({"ai_provider.configure"})
    assert client.post(path, json=registration()).status_code == 201
    assert client.get("/api/v1/admin/agent-harness/boostgame/overview").status_code == 403

    principal.active_tenant_id = "other-tenant"
    assert client.post(path, json=registration(version="v2")).status_code == 403
    assert client.get("/api/v1/admin/agent-harness/boostgame/overview").status_code == 403
    assert counts(db) == (1, 1)


@pytest.mark.parametrize("change", [
    {"enabled": True},
    {"execution_enabled": True},
    {"command": "rm -rf /"},
    {"preferred_executor": "arbitrary-shell"},
    {"capabilities": ["arbitrary_capability"]},
    {"allowed_tools": ["admin.delete"]},
    {"allowed_tools": ["brief.read", "brief.read"]},
    {"capabilities": ["generate_text", "generate_text"]},
    {"fallback_executors": ["codex"]},
    {"max_concurrent": 33},
    {"max_retries": -1},
    {"version": "latest"},
    {"agent_id": "../traversal"},
    {"reason": "bad\nmultiline"},
    {"reason": "bad"},
])
def test_rejects_unsafe_or_invalid_catalog_payload(change, fixture):
    client, db, principal, flags, _ = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    result = client.post(
        "/api/v1/admin/agent-harness/boostgame/agents", json=registration(**change),
    )
    assert result.status_code in {409, 422}, result.text
    assert counts(db) == (0, 0)


def test_failed_audit_prevents_catalog_commit(monkeypatch, fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True

    def reject_audit(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(module, "AuthAuditEventModel", reject_audit)
    response = client.post(
        "/api/v1/admin/agent-harness/boostgame/agents", json=registration(),
    )
    assert response.status_code == 500
    assert counts(db) == (0, 0)


def test_no_anonymous_registration_or_read(fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    from fastapi import HTTPException
    from app.modules.authorization.principal import require_authenticated_principal

    def unauthenticated():
        raise HTTPException(status_code=401, detail="Authentication required")

    client.app.dependency_overrides[require_authenticated_principal] = unauthenticated
    assert client.post("/api/v1/admin/agent-harness/boostgame/agents", json=registration()).status_code == 401
    assert client.get("/api/v1/admin/agent-harness/boostgame/overview").status_code == 401
    assert counts(db) == (0, 0)
