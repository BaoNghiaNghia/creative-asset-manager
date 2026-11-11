"""Read-only Codex/Browser readiness and dry-run security regressions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.executor_readiness import (
    ExecutorSnapshot, browser_snapshot, preview_route, probe_codex,
)
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.registry import AgentDefinition
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.realistic_review_ugc.model import RrugcCodexAccountConfigModel


def active_codex_row(tenant: str = "boostgame", account_id: str = "account-1",
                     *, enabled: bool = True, slots: int = 2,
                     stages: tuple[str, ...] = ("stage1", "stage2")):
    return RrugcCodexAccountConfigModel(
        tenant_id=tenant, account_id=account_id, display_name="Codex",
        enabled=enabled, max_concurrent=slots, stages_json=list(stages),
    )


@pytest.fixture
def fixture(monkeypatch):
    from app.modules.agent_harness import router as route_module
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
        RrugcCodexAccountConfigModel.__table__,
    ])
    db = Session(engine, expire_on_commit=False)
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", AgentDefinition(
        agent_id="gatorhats.typography", version="v1",
        capabilities=frozenset({"generate_text", "image_edit"}),
        allowed_tools=frozenset({"design.read", "brief.read"}),
        preferred_executor="codex", fallback_executors=("browser",),
        max_concurrent=2, max_retries=1,
    ), actor_id="operator")
    repo.register_definition("other", AgentDefinition(
        agent_id="gatorhats.typography", version="v1",
        capabilities=frozenset({"image_edit"}), allowed_tools=frozenset({"design.read"}),
        preferred_executor="codex", max_concurrent=1,
    ), actor_id="other-operator")
    db.commit()

    principal = SimpleNamespace(
        active_tenant_id="boostgame", user_id="operator",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}), platform_admin=False,
    )
    flags = SimpleNamespace(
        AGENT_HARNESS_READ_API_ENABLED=False,
        AGENT_HARNESS_MANAGEMENT_API_ENABLED=False,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
        CODEX_IMAGE_HOME="/does-not-exist-or-run",
        CODEX_IMAGE_BINARY="codex",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    monkeypatch.setattr(route_module, "get_settings", lambda: flags)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, principal, flags, route_module
    db.close()
    engine.dispose()


def preview_json(**changes):
    value = {
        "stage": "stage1",
        "required_capabilities": ["image_edit"],
        "required_tools": ["design.read"],
    }
    value.update(changes)
    return value


def preview_url(tenant="boostgame", version="v1"):
    return f"/api/v1/admin/agent-harness/{tenant}/agents/gatorhats.typography/{version}/route-preview"


def observed_codex(*, slots=3, busy=0):
    return ExecutorSnapshot(
        "codex", "observed_capacity" if busy < slots else "capacity_exhausted",
        configured_slots=slots, busy_slots=busy,
        available_slots=slots - busy,
        capabilities=frozenset({"generate_text", "image_edit"}),
    )


def test_no_route_is_exposed_when_flag_off(fixture):
    client, db, principal, flags, module = fixture
    assert client.post(preview_url(), json=preview_json()).status_code == 404
    flags.AGENT_HARNESS_MANAGEMENT_API_ENABLED = True
    assert client.post(preview_url(), json=preview_json()).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    ok = client.post(preview_url(), json=preview_json())
    assert ok.status_code == 200
    assert ok.json()["reason"] == "no_verified_executor_capacity"
    assert ok.json()["dispatch_allowed"] is False
    assert ok.json()["executor_observations"][1]["status"] == "provider_unverified"
    assert not list(db.scalars(select(RrugcCodexAccountConfigModel)))


def test_preview_advises_existing_codex_capacity_but_never_dispatches(monkeypatch, fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    monkeypatch.setattr(module, "probe_codex", lambda *a, **kw: observed_codex(slots=3))
    result = client.post(preview_url(), json=preview_json())
    assert result.status_code == 200
    data = result.json()
    assert data["reason"] == "agent_inactive"
    assert data["candidate_executor"] == "codex"
    assert data["candidate_priority"] == "preferred"
    assert data["advisory_agent_slot_limit"] == 2
    assert data["slot_counts_are_advisory"] is True
    assert data["execution_enabled"] is False
    assert data["dispatch_allowed"] is False
    agent = db.get(AgentDefinitionModel, ("boostgame", "gatorhats.typography", "v1"))
    assert agent.enabled is False


@pytest.mark.parametrize("change,reason,probed", [
    ({"required_tools": ["stage2.reference.read"]}, "tool_not_permitted", False),
    ({"required_capabilities": ["image_edit", "generate_text"], "stage": "stage2"},
     "stage_capability_unsupported", False),
    ({"required_capabilities": ["generate_text"], "stage": "stage4"},
     "stage_capability_unsupported", False),
])
def test_preview_never_escalates_tools_or_stage_capabilities(monkeypatch, fixture, change, reason, probed):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    observed = []
    def fake_probe(*args, **kwargs):
        observed.append(True)
        return observed_codex()
    monkeypatch.setattr(module, "probe_codex", fake_probe)
    result = client.post(preview_url(), json=preview_json(**change))
    assert result.status_code == 200, result.text
    assert result.json()["reason"] == reason
    assert result.json()["dispatch_allowed"] is False
    assert bool(observed) == probed


def test_emergency_stop_skips_cli_probe(monkeypatch, fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    flags.AI_EMERGENCY_STOP_ENABLED = True
    monkeypatch.setattr(module, "probe_codex", lambda *a, **kw: pytest.fail("emergency stop must block probe"))
    result = client.post(preview_url(), json=preview_json())
    assert result.status_code == 200
    assert result.json()["reason"] == "emergency_stop"
    assert result.json()["candidate_executor"] is None
    assert result.json()["dispatch_allowed"] is False


def test_cross_tenant_missing_version_and_permission_fail_before_probe(monkeypatch, fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    monkeypatch.setattr(module, "probe_codex", lambda *a, **kw: pytest.fail("unauthorized probe"))
    assert client.post(preview_url("other"), json=preview_json()).status_code == 403
    assert client.post(preview_url(version="v2"), json=preview_json()).status_code == 404
    principal.effective_permissions = frozenset({"ai_provider.configure"})
    assert client.post(preview_url(), json=preview_json()).status_code == 403
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.post(preview_url(), json=preview_json()).status_code == 403
    app = client.app
    def deny_login():
        raise HTTPException(status_code=401, detail="unauthenticated")
    app.dependency_overrides[require_authenticated_principal] = deny_login
    assert client.post(preview_url(), json=preview_json()).status_code == 401


@pytest.mark.parametrize("bad", [
    {"stage": "any"},
    {"required_capabilities": []},
    {"required_capabilities": ["run_shell"]},
    {"required_tools": ["admin.delete"]},
    {"required_tools": ["design.read", "design.read"]},
    {"required_capabilities": ["image_edit", "image_edit"]},
    {"command": "whoami"},
    {"execution_enabled": True},
])
def test_rejects_untrusted_routing_input(bad, fixture):
    client, db, principal, flags, module = fixture
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    result = client.post(preview_url(), json=preview_json(**bad))
    assert result.status_code == 422, result.text


@pytest.mark.parametrize("state,active,slots,expected", [
    ("connected", 0, 2, "observed_capacity"),
    ("connected", 2, 2, "capacity_exhausted"),
    ("auth_required", 0, 2, "login_not_verified"),
    ("cli_unavailable", 0, 2, "login_not_verified"),
])
def test_real_rrugc_account_config_probe_is_read_only(
    monkeypatch, fixture, state, active, slots, expected,
):
    from app.modules.agent_harness import executor_readiness as checks
    client, db, principal, flags, module = fixture
    db.add(active_codex_row(slots=slots))
    db.add(active_codex_row("other", slots=2))
    db.commit()
    calls = []
    def stub_login(account_id, codex_home, codex_binary):
        calls.append(account_id)
        return state
    monkeypatch.setattr(checks, "auth_status", stub_login)
    monkeypatch.setattr(checks, "active_slots", lambda *a, **kw: active)
    before = db.scalar(select(func.count()).select_from(RrugcCodexAccountConfigModel))
    status = probe_codex(db, "boostgame", "stage1", codex_home="/nonexistent", codex_binary="codex")
    after = db.scalar(select(func.count()).select_from(RrugcCodexAccountConfigModel))
    assert before == after == 2
    assert calls == ["account-1"]
    assert status.status == expected
    assert status.available_slots == (slots - active if state == "connected" else 0)
    assert status.configured_slots == (slots if state == "connected" else 0)
    assert "token" not in str(status.document()).lower()
    assert "nonexistent" not in str(status.document()).lower()


def test_browser_prototype_never_advertises_verified_capacity(monkeypatch):
    from app.modules.agent_harness import executor_readiness as checks
    for ready in (False, True):
        monkeypatch.setattr(checks, "BROWSER_EXTENSION_READY", ready)
        result = browser_snapshot()
        assert result.available_slots == 0
        assert result.configured_slots == 0
        assert result.status == ("heartbeat_unavailable" if ready else "provider_unverified")


def test_fail_closed_on_probe_exception(monkeypatch, fixture):
    from app.modules.agent_harness import executor_readiness as checks
    client, db, principal, flags, module = fixture
    db.add(active_codex_row())
    db.commit()
    monkeypatch.setattr(checks, "auth_status", lambda *a, **kw: (_ for _ in ()).throw(OSError("no cli")))
    result = probe_codex(db, "boostgame", "stage1", codex_home="/secret", codex_binary="codex")
    assert result.status == "login_not_verified"
    assert result.available_slots == 0


@pytest.mark.parametrize("status,configured,busy,available", [
    ("login_not_verified", 2, 0, 2),
    ("heartbeat_unavailable", 1, 0, 1),
    ("capacity_exhausted", 2, 0, 2),
    ("observed_capacity", 2, 0, 3),  # impossible positive capacity
    ("observed_capacity", 2, 2, 1),  # stale/inconsistent count
    ("observed_capacity", 2, -1, 3),
    ("observed_capacity", True, 0, 1),  # bool is not an int slot count
])
def test_route_rejects_unverified_and_incoherent_executor_capacity(
    fixture, status, configured, busy, available,
):
    _, db, _, _, _ = fixture
    agent = db.get(AgentDefinitionModel, ("boostgame", "gatorhats.typography", "v1"))
    broken = ExecutorSnapshot(
        "codex", status, configured, busy, available,
        frozenset({"image_edit"}),
    )
    assert broken.verified_advisory_capacity(frozenset({"image_edit"})) is False
    result = preview_route(
        agent, stage="stage1",
        required_capabilities=frozenset({"image_edit"}),
        required_tools=frozenset({"design.read"}),
        codex=broken, browser=browser_snapshot(),
        global_emergency_stop=False,
    )
    assert result["reason"] == "no_verified_executor_capacity"
    assert result["candidate_executor"] is None
    assert result["candidate_priority"] is None
    assert result["advisory_agent_slot_limit"] == 0
    assert result["dispatch_allowed"] is False


def test_fallback_only_after_verified_positive_and_coherent_snapshot(fixture):
    _, db, _, _, _ = fixture
    agent = db.get(AgentDefinitionModel, ("boostgame", "gatorhats.typography", "v1"))
    codex = ExecutorSnapshot(
        "codex", "capacity_exhausted", 2, 2, 0,
        frozenset({"image_edit", "generate_text"}),
    )
    browser_unverified = ExecutorSnapshot(
        "browser", "heartbeat_unavailable", 1, 0, 1, frozenset({"image_edit"}),
    )
    args = dict(
        agent=agent, stage="stage1",
        required_capabilities=frozenset({"image_edit"}),
        required_tools=frozenset({"design.read"}),
        codex=codex, global_emergency_stop=False,
    )
    blocked = preview_route(**args, browser=browser_unverified)
    assert blocked["candidate_executor"] is None
    assert blocked["dispatch_allowed"] is False

    # Simulated trusted probe only. Real browser_snapshot() never advertises
    # this state until an authenticated, independently verified bridge exists.
    verified_fallback = ExecutorSnapshot(
        "browser", "observed_capacity", 1, 0, 1, frozenset({"image_edit"}),
    )
    proposed = preview_route(**args, browser=verified_fallback)
    assert proposed["candidate_executor"] == "browser"
    assert proposed["candidate_priority"] == "fallback"
    assert proposed["advisory_agent_slot_limit"] == 1
    assert proposed["reason"] == "agent_inactive"
    assert proposed["dispatch_allowed"] is False


def test_browser_only_agent_never_runs_unconfigured_codex_probe(monkeypatch, fixture):
    client, db, _, flags, module = fixture
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", AgentDefinition(
        agent_id="gatorhats.typography", version="v2",
        capabilities=frozenset({"image_edit"}),
        allowed_tools=frozenset({"design.read"}),
        preferred_executor="browser", fallback_executors=(),
    ), actor_id="operator")
    db.commit()
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    monkeypatch.setattr(module, "probe_codex", lambda *a, **kw: pytest.fail(
        "browser-only agent must not run Codex probes",
    ))
    result = client.post(preview_url(version="v2"), json=preview_json())
    assert result.status_code == 200
    payload = result.json()
    assert payload["reason"] == "no_verified_executor_capacity"
    assert payload["candidate_executor"] is None
    assert payload["executor_observations"][0]["status"] == "not_probed"
    assert payload["dispatch_allowed"] is False
