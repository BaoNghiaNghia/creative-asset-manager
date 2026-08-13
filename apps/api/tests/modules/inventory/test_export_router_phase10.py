from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory import router as inventory_router
from app.modules.inventory.exports.service import ExportResult, InventoryExportFailure

DAY = date(2030, 8, 9)


class _Exports:
    """Route-level test double: authorization stays fully real."""

    calls: list[tuple[str, date]] = []
    rows: dict[tuple[str, date], ExportResult] = {}
    failure: str | None = None

    def __init__(self, _factory):
        pass

    def get(self, tenant_id: str, business_date: date):
        return self.rows.get((tenant_id, business_date))

    def export(self, tenant_id: str, business_date: date, _actor_id: str | None = None):
        self.calls.append((tenant_id, business_date))
        if self.failure:
            raise InventoryExportFailure(self.failure)
        key = (tenant_id, business_date)
        if key not in self.rows:
            self.rows[key] = ExportResult(
                id=f"export-{tenant_id}", business_date=business_date, status="completed",
                main_drive_file_id="main", backup_drive_file_id="backup", content_sha256="sha",
                completed_at=None, error_code=None, archive_status="completed", archive_error_code=None,
            )
        return self.rows[key]


class ExportRouterPhase10Test(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(inventory_router.router)
        self.client = TestClient(self.app)
        self.exporter_a = CurrentPrincipal("a-user", "a", "member-a", None, frozenset(), frozenset({"inventory.read", "inventory.export"}), False, "s", "test")
        self.reader_a = CurrentPrincipal("a-reader", "a", "member-a", None, frozenset(), frozenset({"inventory.read"}), False, "s", "test")
        self.exporter_b = CurrentPrincipal("b-user", "b", "member-b", None, frozenset(), frozenset({"inventory.read", "inventory.export"}), False, "s", "test")
        _Exports.calls, _Exports.rows, _Exports.failure = [], {}, None
        self.service = patch("app.modules.inventory.router.InventoryExportService", _Exports)
        self.session = patch("app.modules.inventory.router.SessionLocal", self.factory)
        self.service.start(); self.session.start()

    def tearDown(self):
        self.service.stop(); self.session.stop(); self.engine.dispose()

    def request(self, principal: CurrentPrincipal | None, method: str, path: str):
        self.app.dependency_overrides.clear()
        if principal is not None:
            for route in self.app.routes:
                if getattr(route, "path", "").startswith("/api/inventory"):
                    for permission_dependency in route.dependant.dependencies:
                        for authenticated_dependency in permission_dependency.dependencies:
                            self.app.dependency_overrides[authenticated_dependency.call] = lambda principal=principal: principal
        return self.client.request(method, path)

    def test_get_requires_export_permission_and_is_tenant_scoped(self):
        _Exports.rows[("a", DAY)] = _Exports(None).export("a", DAY)
        self.assertEqual(200, self.request(self.exporter_a, "GET", f"/api/inventory/exports/{DAY}").status_code)
        self.assertEqual(403, self.request(self.reader_a, "GET", f"/api/inventory/exports/{DAY}").status_code)
        self.assertEqual(401, self.request(None, "GET", f"/api/inventory/exports/{DAY}").status_code)
        self.assertEqual(404, self.request(self.exporter_b, "GET", f"/api/inventory/exports/{DAY}").status_code)
        self.assertEqual(404, self.request(self.exporter_a, "GET", "/api/inventory/exports/2030-08-10").status_code)

    def test_post_requires_permission_is_idempotent_and_never_crosses_tenants(self):
        self.assertEqual(403, self.request(self.reader_a, "POST", f"/api/inventory/exports/{DAY}").status_code)
        first = self.request(self.exporter_a, "POST", f"/api/inventory/exports/{DAY}")
        second = self.request(self.exporter_a, "POST", f"/api/inventory/exports/{DAY}")
        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(2, len(_Exports.calls))
        self.assertTrue(all(tenant == "a" for tenant, _ in _Exports.calls))
        self.assertEqual(404, self.request(self.exporter_b, "GET", f"/api/inventory/exports/{DAY}").status_code)

    def test_post_surfaces_unfinalized_and_sheet_four_fail_closed_without_bypass(self):
        _Exports.failure = "inventory_daily_run_not_finalized"
        response = self.request(self.exporter_a, "POST", f"/api/inventory/exports/{DAY}")
        self.assertEqual(409, response.status_code)
        _Exports.failure = "inventory_sheet4_invariant_failed"
        response = self.request(self.exporter_a, "POST", f"/api/inventory/exports/{DAY}")
        self.assertEqual(422, response.status_code)
        self.assertEqual("inventory_sheet4_invariant_failed", response.json()["detail"]["code"])


if __name__ == "__main__":
    unittest.main()