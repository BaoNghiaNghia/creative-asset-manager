from datetime import date
from unittest.mock import Mock, patch

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
    assert client.post("/api/inventory/daily-sheet/snapshot/run", json={}).status_code == 403
    assert client.post("/api/inventory/daily-sheet/reconcile/run", json={"dry_run": True}).status_code == 403


def test_daily_sheet_manual_routes_forward_tenant_date_and_dry_run():
    service = Mock()
    service.snapshot_and_reset.return_value = Mock(id="snapshot", business_date=date(2030, 8, 9), status="completed", snapshot_file_id="copy")
    service.reconcile.return_value = {"status": "dry_run", "writes": 0}
    client = client_for(principal({"inventory.read", "inventory.finalize"}))
    with patch("app.modules.inventory.daily_sheet.router._service", return_value=service):
        snapshot = client.post("/api/inventory/daily-sheet/snapshot/run", json={"business_date": "2030-08-09"})
        preview = client.post("/api/inventory/daily-sheet/reconcile/run", json={"business_date": "2030-08-09", "dry_run": True})
    assert snapshot.status_code == 200
    assert preview.status_code == 200
    service.snapshot_and_reset.assert_called_once_with("tenant-a", date(2030, 8, 9))
    service.reconcile.assert_called_once_with("tenant-a", date(2030, 8, 9), dry_run=True)
