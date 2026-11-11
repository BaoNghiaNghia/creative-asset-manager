"""Phase 4B.1: lease-bound advisory routing must never dispatch or leak tenants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.job_lease_preview import observe_processing_lease
from app.modules.agent_harness.model import AgentDefinitionModel
from app.modules.agent_harness.registry import AgentDefinition
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.router import router
from app.modules.agent_harness.executor_readiness import ExecutorSnapshot
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.processing.model import ProcessingJobModel


def _job(*, tenant="boostgame", job_type="rrugc_creative_brief_generate",
         status="processing", now=None, **changes):
    now = now or datetime.now(timezone.utc)
    data = dict(
        id=str(uuid4()),
        tenant_id=tenant, job_type=job_type,
        entity_type="keyword", entity_id=str(uuid4()),
        idempotency_key=str(uuid4()), payload_json={},
        status=status, attempt_count=1, max_attempts=5,
        claimed_by="image-worker-A",
        claimed_at=now - timedelta(seconds=25),
        updated_at=now - timedelta(seconds=4),
        lease_expires_at=now + timedelta(seconds=50),
        cancellation_requested=False,
    )
    data.update(changes)
    return ProcessingJobModel(**data)


def _body(*, stage="stage1", tools=None):
    return {
        "stage": stage,
        "required_capabilities": ["image_edit"],
        "required_tools": ["design.read", "job.read"] if tools is None else tools,
    }


def _url(job, *, tenant="boostgame", version="v1"):
    return (
        f"/api/v1/admin/agent-harness/{tenant}/agents/"
        f"gatorhats.typography/{version}/jobs/{job.id}/lease-preview"
    )


@pytest.fixture
def setup(monkeypatch):
    from app.modules.agent_harness import router as route_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, ProcessingJobModel.__table__,
    ])
    session = Session(engine, expire_on_commit=False)
    repo = AgentHarnessRepository(session)
    repo.register_definition("boostgame", AgentDefinition(
        agent_id="gatorhats.typography", version="v1",
        capabilities=frozenset({"image_edit"}),
        allowed_tools=frozenset({"design.read", "job.read"}),
        preferred_executor="codex", fallback_executors=("browser",),
        max_concurrent=2,
    ), actor_id="admin")
    repo.register_definition("other", AgentDefinition(
        agent_id="gatorhats.typography", version="v1",
        capabilities=frozenset({"image_edit"}),
        allowed_tools=frozenset({"design.read", "job.read"}),
        preferred_executor="codex",
    ), actor_id="admin")
    session.commit()
    principal = SimpleNamespace(
        active_tenant_id="boostgame", user_id="admin",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    flags = SimpleNamespace(
        AGENT_HARNESS_READ_API_ENABLED=True,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
        CODEX_IMAGE_HOME="/does-not-run",
        CODEX_IMAGE_BINARY="codex",
        WORKER_HEARTBEAT_SECONDS=15.0,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    monkeypatch.setattr(route_module, "get_settings", lambda: flags)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, session, principal, flags, route_module
    session.close()
    engine.dispose()


def _codex_snapshot():
    return ExecutorSnapshot(
        "codex", "observed_capacity", 2, 1, 1,
        frozenset({"image_edit", "generate_text"}),
    )


def test_existing_job_lease_is_advisory_only_and_read_only(monkeypatch, setup):
    client, db, _, _, route_module = setup
    job = _job()
    db.add(job)
    db.commit()
    called = []
    def fake_probe(*args, **kwargs):
        called.append(True)
        return _codex_snapshot()
    monkeypatch.setattr(route_module, "probe_codex", fake_probe)
    before = (job.status, job.claimed_by, job.lease_expires_at, job.attempt_count)
    response = client.post(_url(job), json=_body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["lease"]["state"] == "active_lease_observed"
    assert result["lease"]["recent_lease_row_activity"] is True
    assert result["lease"]["worker_heartbeat_authenticated"] is False
    assert result["lease"]["executor_slot_reserved"] is False
    assert result["reason"] == "lease_observed_advisory_only"
    assert result["route_preview"]["candidate_executor"] == "codex"
    assert result["route_preview"]["dispatch_allowed"] is False
    assert result["dispatch_allowed"] is False
    assert result["execution_enabled"] is False
    assert called == [True]
    assert len(db.dirty) == 0
    assert before == (job.status, job.claimed_by, job.lease_expires_at, job.attempt_count)
    assert "image-worker-A" not in response.text
    assert "idempotency_key" not in response.text


@pytest.mark.parametrize("mutations,reason", [
    ({"status": "pending"}, "job_not_processing"),
    ({"status": "retry"}, "job_not_processing"),
    ({"status": "completed"}, "job_not_processing"),
    ({"claimed_by": None}, "lease_owner_missing"),
    ({"claimed_by": "   "}, "lease_owner_missing"),
    ({"cancellation_requested": True}, "cancellation_requested"),
    ({"attempt_count": 0}, "attempt_not_valid"),
    ({"lease_expires_at": None}, "lease_timestamps_missing"),
    ({"claimed_at": None}, "lease_timestamps_missing"),
    ({"job_type": "rrugc_generate"}, "job_type_not_supported_for_stage"),
])
def test_non_admissible_job_does_not_probe_or_reserve(monkeypatch, setup, mutations, reason):
    client, db, _, _, route_module = setup
    job = _job(**mutations)
    db.add(job)
    db.commit()
    monkeypatch.setattr(route_module, "probe_codex", lambda *a, **kw: pytest.fail(
        "ineligible lease must not run CLI probe",
    ))
    result = client.post(_url(job), json=_body())
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["reason"] == reason
    assert data["route_preview"] is None
    assert data["dispatch_allowed"] is False


def test_expired_and_stale_leases_skip_executor_probe(monkeypatch, setup):
    client, db, _, _, route_module = setup
    expired = _job(lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    stale = _job(
        claimed_at=datetime.now(timezone.utc)-timedelta(seconds=240),
        updated_at=datetime.now(timezone.utc)-timedelta(seconds=200),
        lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=350),
    )
    db.add_all([expired, stale])
    db.commit()
    monkeypatch.setattr(route_module, "probe_codex", lambda *a, **kw: pytest.fail(
        "expired/stale lease cannot be probed",
    ))
    for job, reason in ((expired, "lease_expired"), (stale, "lease_row_activity_stale")):
        response = client.post(_url(job), json=_body())
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == reason
        assert response.json()["route_preview"] is None


def test_stage_without_verified_job_mapping_is_blocked(monkeypatch, setup):
    client, db, _, _, route_module = setup
    job = _job(job_type="rrugc_generate")
    db.add(job)
    db.commit()
    monkeypatch.setattr(route_module, "probe_codex", lambda *a, **kw: pytest.fail(
        "stage4 lease path is not verified",
    ))
    result = client.post(_url(job), json=_body(stage="stage4"))
    assert result.status_code == 200
    assert result.json()["reason"] == "job_type_not_supported_for_stage"
    assert result.json()["route_preview"] is None


def test_cross_tenant_and_missing_job_are_indistinguishable(monkeypatch, setup):
    client, db, principal, _, route_module = setup
    foreign = _job(tenant="other")
    db.add(foreign)
    db.commit()
    monkeypatch.setattr(route_module, "probe_codex", lambda *a, **kw: pytest.fail(
        "cross tenant must not probe",
    ))
    assert client.post(_url(foreign), json=_body()).status_code == 404
    unknown = _job()
    assert client.post(_url(unknown), json=_body()).status_code == 404
    assert client.post(_url(foreign, tenant="other"), json=_body()).status_code == 403
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.post(_url(foreign), json=_body()).status_code == 403


def test_job_read_tool_and_emergency_stop_fail_before_codex_probe(monkeypatch, setup):
    client, db, principal, flags, route_module = setup
    job = _job()
    db.add(job)
    db.commit()
    monkeypatch.setattr(route_module, "probe_codex", lambda *a, **kw: pytest.fail(
        "tool permission / stop should skip all probes",
    ))
    no_tool = client.post(_url(job), json=_body(tools=["design.read"]))
    assert no_tool.status_code == 403
    assert no_tool.json()["detail"] == "Agent job read tool not permitted"
    repo = AgentHarnessRepository(db)
    repo.register_definition("boostgame", AgentDefinition(
        agent_id="gatorhats.typography", version="v2",
        capabilities=frozenset({"image_edit"}),
        allowed_tools=frozenset({"design.read"}),
        preferred_executor="codex",
    ), actor_id="admin")
    db.commit()
    no_pinned_grant = client.post(_url(job, version="v2"), json=_body())
    assert no_pinned_grant.status_code == 403

    flags.AI_EMERGENCY_STOP_ENABLED = True
    stopped = client.post(_url(job), json=_body())
    assert stopped.status_code == 200
    assert stopped.json()["reason"] == "emergency_stop"
    assert stopped.json()["route_preview"] is None
    flags.AI_EMERGENCY_STOP_ENABLED = False
    flags.AGENT_HARNESS_READ_API_ENABLED = False
    assert client.post(_url(job), json=_body()).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.post(_url(job, version="v99"), json=_body()).status_code == 404
    assert client.post(_url(job).replace(job.id, "invalid"), json=_body()).status_code == 422


@pytest.mark.parametrize("stage,job_type,other_stage", [
    ("stage1", "rrugc_creative_brief_generate", "stage2"),
    ("stage1", "rrugc_keyword_image_generate", "stage2"),
    ("stage2", "rrugc_stage2_generate", "stage1"),
])
def test_only_exact_reviewed_stage_job_types_have_live_lease_evidence(
    stage, job_type, other_stage,
):
    now = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    job = _job(now=now, job_type=job_type)
    admitted = observe_processing_lease(job, stage=stage, now=now)
    assert admitted["active_lease_observed"] is True
    assert admitted["job_type_verified_for_stage"] is True
    disallowed = observe_processing_lease(job, stage=other_stage, now=now)
    assert disallowed["active_lease_observed"] is False
    assert disallowed["state"] == "job_type_not_supported_for_stage"


def test_lease_timestamps_and_attempts_fail_closed_without_clock_guessing():
    now = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    valid = _job(
        now=now, claimed_at=now-timedelta(seconds=30),
        updated_at=now-timedelta(seconds=10), lease_expires_at=now+timedelta(seconds=30),
    )
    assert observe_processing_lease(valid, stage="stage1", now=now)["active_lease_observed"]
    for updates, reason in (
        ({"claimed_at": now+timedelta(seconds=20)}, "claim_clock_invalid"),
        ({"updated_at": now+timedelta(seconds=20)}, "lease_activity_clock_invalid"),
        ({"updated_at": now-timedelta(seconds=40)}, "lease_activity_clock_invalid"),
        ({"attempt_count": 6}, "attempt_not_valid"),
        ({"lease_expires_at": now-timedelta(seconds=1)}, "lease_expired"),
    ):
        timestamps = dict(
            claimed_at=now-timedelta(seconds=30),
            updated_at=now-timedelta(seconds=10),
            lease_expires_at=now+timedelta(seconds=30),
        )
        timestamps.update(updates)
        altered = _job(now=now, **timestamps)
        assert observe_processing_lease(altered, stage="stage1", now=now)["state"] == reason
