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


def test_v4_manual_endpoint_forces_shadow_and_ignores_automation_flag():
    service = Mock()
    service.is_agent_v4_configured.return_value = True
    service.run_agent_v4_shadow.return_value = {
        "version": 4,
        "mode": "gemini_tool_sheet_agent",
        "apply_mode": "shadow",
        "status": "shadow",
        "tenant_id": "tenant-a",
        "spreadsheet_file_id": "sheet-1",
        "business_date": "2030-08-09",
        "tool_rounds": 2,
        "read_calls": 1,
        "read_cells": 4,
        "plan_hash": "a" * 64,
        "staged": {"status": "ready", "operations": [], "issues": [], "material_actions": []},
        "writes": 0,
    }
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        response = client.post(
            "/api/inventory/daily-sheet/agent-v4/run",
            json={"business_date": "2030-08-09", "apply_mode": "auto"},
        )
    assert response.status_code == 200
    assert response.json()["apply_mode"] == "shadow"
    assert response.json()["writes"] == 0
    service.run_agent_v4_shadow.assert_called_once_with(
        "tenant-a", date(2030, 8, 9)
    )


def test_v4_configuration_cannot_enable_scheduler():
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
        "version": 4,
        "mode": "gemini_tool_sheet_agent",
        "source": {"allowed_sheets": ["Daily"]},
        "agent": {"apply_mode": "shadow"},
    }
    with patch("app.modules.inventory.daily_sheet.router.SessionLocal", return_value=session):
        response = client.put(
            "/api/inventory/daily-sheet/configuration",
            json={
                "daily_sheet_automation_enabled": True,
                "working_spreadsheet_file_id": "working",
                "config": config,
            },
        )
    assert response.status_code == 200
    assert row.daily_sheet_automation_enabled is False
    assert row.daily_sheet_config_json["version"] == 4
