from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory.daily_sheet.router import router


def principal(permissions):
    return CurrentPrincipal("user", "tenant-a", "member", None, frozenset(), frozenset(permissions), False, "session", "test")


def client_for(current):
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for permission_dependency in route.dependant.dependencies:
                for authenticated_dependency in permission_dependency.dependencies:
                    app.dependency_overrides[authenticated_dependency.call] = lambda: current
    return TestClient(app)


def test_daily_sheet_manual_routes_require_finalize_permission():
    client = client_for(principal({"inventory.read"}))
    assert client.post("/api/inventory/daily-sheet/validate-config").status_code == 403
    assert client.post("/api/inventory/daily-sheet/discover", json={"working_spreadsheet_file_id": "sheet"}).status_code == 403
    assert client.post("/api/inventory/daily-sheet/snapshot/run", json={}).status_code == 403
    assert client.post("/api/inventory/daily-sheet/agent/plan", json={}).status_code == 403
    assert client.post("/api/inventory/daily-sheet/reconcile/run", json={"dry_run": True}).status_code == 403


def test_daily_sheet_manual_routes_forward_tenant_date_and_dry_run():
    service = Mock()
    service.snapshot_and_reset.return_value = Mock(id="snapshot", business_date=date(2030, 8, 9), status="completed", snapshot_file_id="copy")
    service.reconcile.return_value = {"status": "dry_run", "writes": 0}
    service.discover.return_value = {"spreadsheet_id": "sheet", "tabs": [], "warnings": []}
    service.is_agent_v3_configured.return_value = False
    service.is_agent_v4_configured.return_value = False
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        snapshot = client.post("/api/inventory/daily-sheet/snapshot/run", json={"business_date": "2030-08-09"})
        preview = client.post("/api/inventory/daily-sheet/reconcile/run", json={"business_date": "2030-08-09", "dry_run": True})
        discovery = client.post("/api/inventory/daily-sheet/discover", json={"working_spreadsheet_file_id": "sheet"})
    assert snapshot.status_code == 200
    assert preview.status_code == 200
    assert discovery.status_code == 200
    service.snapshot_and_reset.assert_called_once_with("tenant-a", date(2030, 8, 9))
    service.reconcile.assert_called_once_with("tenant-a", date(2030, 8, 9), dry_run=True)
    service.discover.assert_called_once_with("tenant-a", "sheet")


def test_v3_manual_plan_forces_dry_run_and_returns_agent_result():
    service = Mock()
    service.is_agent_v3_configured.return_value = True
    service.plan_agent_run.return_value = {
        "status": "shadow",
        "tenant_id": "tenant-a",
        "business_date": "2030-08-09",
        "source_hash": "source",
        "plan_hash": "plan",
        "plan": {"status": "ready", "operations": []},
        "operation_count": 0,
    }
    client = client_for(principal({"inventory.read", "inventory.finalize"}))

    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = client.post(
            "/api/inventory/daily-sheet/agent/plan",
            json={"business_date": "2030-08-09", "dry_run": False},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "shadow"
    service.plan_agent_run.assert_called_once_with(
        "tenant-a", date(2030, 8, 9), dry_run=True
    )


def test_v3_snapshot_run_rejects_before_accessing_legacy_row_fields():
    service = Mock()
    service.is_agent_v3_configured.return_value = True
    service.is_agent_v4_configured.return_value = False
    client = client_for(principal({"inventory.read", "inventory.finalize"}))

    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = client.post(
            "/api/inventory/daily-sheet/snapshot/run",
            json={"business_date": "2030-08-09"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "gemini_sheet_agent_use_plan_endpoint"
    service.snapshot_and_reset.assert_not_called()


def test_v3_shadow_configuration_stays_shadow_and_automation_disabled():
    row = SimpleNamespace(
        image_pipeline_enabled=True,
        daily_sheet_automation_enabled=True,
        daily_working_spreadsheet_file_id="working",
        daily_archive_root_folder_id=None,
        daily_template_spreadsheet_file_id=None,
        daily_target_spreadsheet_file_id=None,
        daily_snapshot_time_local="05:50",
        daily_reconcile_time_local="07:00",
        timezone="Asia/Ho_Chi_Minh",
        daily_sheet_config_json={},
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = row
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    config = {
        "version": 3,
        "mode": "gemini_sheet_agent",
        "source": {"sheet": "Daily", "range": "A1:H40"},
        "agent": {"apply_mode": "shadow"},
    }

    with patch("app.modules.inventory.daily_sheet.router.SessionLocal", return_value=session):
        response = client.put(
            "/api/inventory/daily-sheet/configuration",
            json={
                "daily_sheet_automation_enabled": False,
                "working_spreadsheet_file_id": "working",
                "config": config,
            },
        )

    assert response.status_code == 200
    assert row.daily_sheet_automation_enabled is False
    assert row.daily_sheet_config_json["agent"]["apply_mode"] == "shadow"


def test_v4_manual_endpoint_uses_configured_live_execution_path():
    service = Mock()
    service.is_agent_v4_configured.return_value = True
    service.run_agent_v4.return_value = {
        "version": 4,
        "mode": "gemini_tool_sheet_agent",
        "apply_mode": "auto",
        "status": "completed",
        "tenant_id": "tenant-a",
        "spreadsheet_file_id": "sheet-1",
        "business_date": "2030-08-09",
        "tool_rounds": 2,
        "read_calls": 1,
        "read_cells": 4,
        "plan_hash": "a" * 64,
        "staged": {"status": "ready", "operations": [], "issues": [], "material_actions": []},
        "writes": 1,
    }
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = client.post(
            "/api/inventory/daily-sheet/agent-v4/run",
            json={"business_date": "2030-08-09"},
        )
    assert response.status_code == 200
    assert response.json()["apply_mode"] == "auto"
    assert response.json()["writes"] == 1
    service.run_agent_v4.assert_called_once_with(
        "tenant-a", date(2030, 8, 9)
    )

def test_v4_configuration_enables_scheduler_only_for_auto_mode():
    row = SimpleNamespace(
        image_pipeline_enabled=True,
        daily_sheet_automation_enabled=False,
        daily_working_spreadsheet_file_id="working",
        daily_archive_root_folder_id=None,
        daily_template_spreadsheet_file_id=None,
        daily_target_spreadsheet_file_id=None,
        daily_snapshot_time_local="05:50",
        daily_reconcile_time_local="07:00",
        timezone="Asia/Ho_Chi_Minh",
        daily_sheet_config_json={},
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = row
    service = Mock()
    service.validate_configuration.return_value = {"valid": True}
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    config = {
        "version": 4,
        "mode": "gemini_tool_sheet_agent",
        "source": {"allowed_sheets": ["Daily"]},
        "agent": {"apply_mode": "auto"},
    }
    with (
        patch("app.modules.inventory.daily_sheet.router.SessionLocal", return_value=session),
        patch("app.modules.inventory.daily_sheet.router._service", return_value=service),
    ):
        response = client.put(
            "/api/inventory/daily-sheet/configuration",
            json={
                "daily_sheet_automation_enabled": True,
                "working_spreadsheet_file_id": "working",
                "config": config,
            },
        )
    assert response.status_code == 200
    assert row.daily_sheet_automation_enabled is True
    assert row.daily_sheet_config_json["version"] == 4
    service.validate_configuration.assert_called_once_with("tenant-a")

def test_lifecycle_history_requires_read_permission_and_forwards_pagination():
    denied = client_for(principal(set()))
    assert denied.get("/api/inventory/daily-sheet/lifecycle-history").status_code == 403
    service = Mock()
    service.lifecycle_history.return_value = {"items": [], "page": 2, "page_size": 50, "total": 0, "pages": 1}
    allowed = client_for(principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = allowed.get("/api/inventory/daily-sheet/lifecycle-history?page=2&page_size=50")
    assert response.status_code == 200
    service.lifecycle_history.assert_called_once_with("tenant-a", page=2, page_size=50)


def test_lifecycle_history_rejects_invalid_pagination():
    service = Mock()
    service.lifecycle_history.side_effect = ValueError("invalid_lifecycle_history_pagination")
    allowed = client_for(principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = allowed.get("/api/inventory/daily-sheet/lifecycle-history?page_size=30")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_lifecycle_history_pagination"

def test_morning_reset_rerun_requires_control_permission_and_calls_scheduler():
    denied = client_for(principal({"inventory.read"}))
    assert denied.post("/api/inventory/daily-sheet/lifecycle-history/2030-08-10/morning-reset/rerun").status_code == 403
    allowed = client_for(principal({"inventory.control"}))
    scheduler = Mock()
    scheduler.retry_v4_morning_reset.return_value = {"status": "completed", "stage": "morning_reset"}
    with patch("app.modules.inventory.daily_sheet.router.InventoryDailyScheduler", return_value=scheduler):
        response = allowed.post("/api/inventory/daily-sheet/lifecycle-history/2030-08-10/morning-reset/rerun")
    assert response.status_code == 200
    scheduler.retry_v4_morning_reset.assert_called_once_with("tenant-a", date(2030, 8, 10))




def test_morning_reset_recovery_preview_and_apply_require_control_and_forward_plan_hash():
    denied = client_for(principal({"inventory.read"}))
    assert denied.post(
        "/api/inventory/daily-sheet/lifecycle-history/2030-08-10/morning-reset/recovery/preview"
    ).status_code == 403

    allowed = client_for(principal({"inventory.control"}))
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = {
        "status": "preview_ready",
        "stage": "morning_reset",
        "business_date": "2030-08-10",
        "plan_hash": "a" * 64,
        "safe_operation_count": 1,
        "write_operation_count": 1,
        "excluded_clear_count": 2,
        "operations": [],
    }
    scheduler.apply_v4_morning_reset_recovery.return_value = {
        "status": "completed",
        "stage": "morning_reset",
        "business_date": "2030-08-10",
        "plan_hash": "a" * 64,
        "applied_count": 1,
        "already_correct_count": 0,
        "excluded_clear_count": 2,
    }
    with patch(
        "app.modules.inventory.daily_sheet.router.InventoryDailyScheduler",
        return_value=scheduler,
    ):
        preview = allowed.post(
            "/api/inventory/daily-sheet/lifecycle-history/2030-08-10/morning-reset/recovery/preview"
        )
        applied = allowed.post(
            "/api/inventory/daily-sheet/lifecycle-history/2030-08-10/morning-reset/recovery/apply",
            json={"plan_hash": "a" * 64},
        )

    assert preview.status_code == 200
    assert applied.status_code == 200
    scheduler.preview_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a", date(2030, 8, 10)
    )
    scheduler.apply_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a",
        date(2030, 8, 10),
        plan_hash="a" * 64,
    )


def test_stage_detail_requires_read_permission_and_forwards_stage():
    denied = client_for(principal(set()))
    assert denied.get(
        "/api/inventory/daily-sheet/lifecycle-history/2030-08-10/stages/evening_reconcile/detail"
    ).status_code == 403
    audit = Mock()
    audit.stage_detail.return_value = {
        "id": "audit-1",
        "business_date": "2030-08-10",
        "stage": "evening_reconcile",
        "changes": [],
    }
    allowed = client_for(principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router._audit", return_value=audit):
        response = allowed.get(
            "/api/inventory/daily-sheet/lifecycle-history/2030-08-10/stages/evening_reconcile/detail"
        )
    assert response.status_code == 200
    audit.stage_detail.assert_called_once_with(
        "tenant-a", date(2030, 8, 10), "evening_reconcile"
    )


def test_historical_replay_errors_expose_retry_metadata():
    class ReplayTransportError(RuntimeError):
        code = "inventory_gemini_transport_error"

    service = Mock()
    service.is_agent_v4_configured.return_value = True
    service.replay_agent_v4_historical.side_effect = ReplayTransportError("temporary upstream timeout")
    allowed = client_for(principal({"inventory.control"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = allowed.post(
            "/api/inventory/daily-sheet/lifecycle-history/2030-08-09/gemini/replay",
            json={"mode": "fresh_copy", "promote": True},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "inventory_gemini_transport_error",
        "message": "temporary upstream timeout",
        "error_category": "TRANSPORT",
        "retryable": True,
    }


def test_knowledge_routes_separate_read_from_control_permission():
    denied = client_for(principal({"inventory.read"}))
    assert denied.post(
        "/api/inventory/daily-sheet/knowledge",
        json={
            "kind": "RULE",
            "title": "Rule",
            "content": "Content",
            "scope_type": "workbook",
            "scope_key": "*",
        },
    ).status_code == 403

    knowledge = Mock()
    knowledge.list.return_value = [{"id": "k1", "status": "active"}]
    knowledge.create.return_value = {"id": "k2", "status": "draft"}
    reader = client_for(principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router._knowledge", return_value=knowledge):
        response = reader.get("/api/inventory/daily-sheet/knowledge?status=active,proposed")
    assert response.status_code == 200
    knowledge.list.assert_called_once_with(
        "tenant-a", statuses=("active", "proposed")
    )

    controller = client_for(principal({"inventory.control"}))
    with patch("app.modules.inventory.daily_sheet.router._knowledge", return_value=knowledge):
        response = controller.post(
            "/api/inventory/daily-sheet/knowledge",
            json={
                "kind": "RULE",
                "title": "Rule",
                "content": "Content",
                "scope_type": "workbook",
                "scope_key": "*",
            },
        )
    assert response.status_code == 200
    knowledge.create.assert_called_once()