"""Read-only CAM Harness P0 operational baseline; no migration or backup."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.modules.agent_harness import router as module
from app.modules.agent_harness import operational_readiness as ops
from app.modules.agent_harness.operational_readiness import (
    disk_headroom, collect_processing_queue_counts, summarize_readiness,
)
from app.modules.authorization.principal import require_authenticated_principal


class StubResult:
    def all(self):
        return [("pending", 5), ("retry", 2), ("processing", 3)]


class StubSession:
    def __init__(self):
        self.executions = []

    def execute(self, query):
        self.executions.append(query)
        return StubResult()


def settings(**extra):
    state = dict(
        AGENT_HARNESS_READ_API_ENABLED=False,
        AGENT_HARNESS_MANAGEMENT_API_ENABLED=False,
        AGENT_HARNESS_EXECUTION_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
    )
    state.update(extra)
    return SimpleNamespace(**state)


def test_disk_headroom_reports_unverified_on_filesystem_error(monkeypatch):
    def fail(mount):
        raise OSError("statvfs unavailable")

    monkeypatch.setattr(ops.os, "statvfs", fail)
    result = disk_headroom()
    assert result["state"] == "unverified"
    assert result["sufficient_for_canary"] is False


def test_disk_threshold_and_execution_never_grants_dispatch(monkeypatch):
    blocks = (50 * 1024**3) // 4096
    available = (6 * 1024**3) // 4096
    monkeypatch.setattr(ops.os, "statvfs",
                        lambda mount: SimpleNamespace(
                            f_frsize=4096, f_blocks=blocks,
                            f_bfree=available, f_bavail=available,
                        ))
    disk = disk_headroom()
    assert disk["sufficient_for_canary"]
    enabled = settings(
        AGENT_HARNESS_EXECUTION_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=True,
    )
    value = summarize_readiness(
        tenant_id="boostgame", settings=enabled,
        open_jobs={"pending": 7, "retry": 2, "processing": 1},
        disk=disk,
    )
    assert value["executor"]["configured_execution_flag"] is True
    assert value["executor"]["dispatch_allowed"] is False
    assert value["executor"]["reservation_created"] is False
    assert value["feedback"]["processing_configured"] is True
    assert value["feedback"]["automatic_promotion_enabled"] is False
    assert value["processing_jobs"] == {"pending": 7, "retry": 2, "processing": 1}
    assert "external_executor_slot_not_reserved" in value["blockers"]
    assert value["no_backup_created"] is True
    assert value["no_new_database"] is True


def test_disk_pressure_blocks_canary_even_when_execution_flag_on():
    value = summarize_readiness(
        tenant_id="boostgame",
        settings=settings(AGENT_HARNESS_EXECUTION_ENABLED=True),
        open_jobs={},
        disk={"state": "disk_pressure", "available_mib": 500,
              "used_percent": 97.8, "sufficient_for_canary": False},
    )
    assert "disk_headroom_unverified_or_low" in value["blockers"]
    assert value["executor"]["dispatch_allowed"] is False


def test_status_query_is_tenant_scoped_and_aggregate_only():
    db = StubSession()
    assert collect_processing_queue_counts(db, tenant_id="boostgame") == {
        "pending": 5, "retry": 2, "processing": 3,
    }
    assert len(db.executions) == 1
    sql = str(db.executions[0])
    assert "tenant_id" in sql and "GROUP BY" in sql
    assert "processing_jobs" in sql


def test_admin_endpoint_is_flagged_tenant_scoped_and_never_writes(monkeypatch):
    db = StubSession()
    cfg = settings()
    actor = SimpleNamespace(
        user_id="test", active_tenant_id="boostgame",
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
        platform_admin=False,
    )
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[require_authenticated_principal] = lambda: actor
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(module, "get_settings", lambda: cfg)
    monkeypatch.setattr(module, "disk_headroom", lambda: {
        "state": "advisory_headroom", "available_mib": 6550,
        "used_percent": 86.3, "sufficient_for_canary": True,
    })
    with TestClient(app) as client:
        url = "/api/v1/admin/agent-harness/boostgame/operational-readiness"
        assert client.get(url).status_code == 404
        cfg.AGENT_HARNESS_READ_API_ENABLED = True
        assert client.get(url).status_code == 200
        body = client.get(url).json()
        assert body["tenant_id"] == "boostgame"
        assert body["processing_jobs"]["processing"] == 3
        assert body["executor"]["dispatch_allowed"] is False
        assert body["no_backup_created"] is True
        assert "jobs" not in body  # No job IDs, actor IDs, payloads or raw prompts.
        assert client.get("/api/v1/admin/agent-harness/another/operational-readiness").status_code == 403
        actor.effective_permissions = frozenset({"ai_operations.read"})
        assert client.get(url).status_code == 403
        assert len(db.executions) == 2
