"""Safety contract for Catinat read-only tenant catalog reconciliation."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory.daily.catinat_catalog_mapping import CatinatCatalogMappingPreview
from app.modules.inventory.daily.catinat_count_confirmations import CatinatCountBlocked
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import InventoryItemModel, InventoryLocationModel

DAY = date(2026, 10, 20)


def readiness(*rows):
    return {
        "mode": "read_only_not_a_signed_close", "business_date": DAY.isoformat(),
        "shift": "evening", "source_hash": "a" * 64,
        "spreadsheet_id": "trusted-bound-sheet",
        "items": list(rows) or [{
            "sheet": "KHO PHA CHẾ", "cell": "L5",
            "material": "SỮA   ĐẶC", "unit": "lon", "form": "whole",
            "raw": "0", "quantity": "0",
        }],
    }


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__, InventoryItemModel.__table__, InventoryLocationModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([
            TenantModel(id="tenant-a", name="Catinat", slug="catinat"),
            TenantModel(id="tenant-b", name="Other", slug="other"),
        ])
        session.add(InventoryItemModel(
            id="milk-a", tenant_id="tenant-a", sku="milk", name="SỮA ĐẶC",
            base_unit="gr", whole_unit="lon", fraction_unit="gr",
            conversion_factor=Decimal(380), active=True,
        ))
        session.add(InventoryLocationModel(
            id="bar-a", tenant_id="tenant-a", code="bar", name="KHO PHA CHẾ", active=True,
        ))
        # This item and location MUST never be leaked into tenant-a's candidate results.
        session.add(InventoryItemModel(
            id="milk-other", tenant_id="tenant-b", sku="milk",
            name="SỮA ĐẶC", base_unit="gr", whole_unit="lon",
            conversion_factor=Decimal(380), active=True,
        ))
        session.add(InventoryLocationModel(
            id="bar-other", tenant_id="tenant-b", code="bar", name="KHO PHA CHẾ",
            active=True,
        ))
    yield CatinatCatalogMappingPreview(sessions), sessions
    engine.dispose()


def test_exact_name_unit_location_are_candidates_only(db):
    service, _ = db
    result = service.inspect(tenant_id="tenant-a", business_date=DAY,
                             current_readiness=readiness)
    assert result["items"][0]["status"] == "exact_name_and_unit_candidate"
    assert result["items"][0]["candidate_item_ids"] == ["milk-a"]
    assert result["locations"][0]["candidate_location_ids"] == ["bar-a"]
    assert result["summary"]["exact_candidates"] == 1
    assert result["catalog_coverage"]["certified"] is False
    assert result["ready_to_sign"] is False
    assert result["inventory_writes"] == result["sheets_writes"] == 0


def test_missing_locations_do_not_promote_item_match(db):
    service, sessions = db
    with sessions.begin() as session:
        session.get(InventoryLocationModel, "bar-a").active = False
    result = service.inspect(tenant_id="tenant-a", business_date=DAY,
                             current_readiness=readiness)
    assert result["items"][0]["status"] == "location_requires_review"
    assert result["locations"][0]["status"] == "no_exact_location"
    assert result["summary"]["requires_review"] == 1


def test_missing_item_and_unit_mismatch_are_not_approved(db):
    service, _ = db
    rows = [
        {"sheet": "KHO PHA CHẾ", "cell": "L5", "material": "SỮA ĐẶC", "unit": "kg", "form": "whole"},
        {"sheet": "KHO PHA CHẾ", "cell": "L6", "material": "MẬT ONG", "unit": "chai", "form": "whole"},
        {"sheet": "KHO PHA CHẾ", "cell": "M5", "material": "SỮA ĐẶC", "unit": "", "form": "loose"},
    ]
    result = service.inspect(tenant_id="tenant-a", business_date=DAY,
                             current_readiness=lambda: readiness(*rows))
    assert [r["status"] for r in result["items"]] == [
        "unit_mismatch", "no_exact_item", "source_unit_missing",
    ]
    assert result["summary"]["exact_candidates"] == 0


def test_ambiguous_same_name_and_location_block_exact_mapping(db):
    service, sessions = db
    with sessions.begin() as session:
        session.add(InventoryItemModel(
            id="milk-alt", tenant_id="tenant-a", sku="milk-alt", name="SỮA ĐẶC",
            base_unit="gr", whole_unit="lon", conversion_factor=Decimal(380), active=True,
        ))
        session.add(InventoryLocationModel(
            id="bar-alt", tenant_id="tenant-a", code="bar-alt",
            name="KHO PHA CHẾ", active=True,
        ))
    result = service.inspect(tenant_id="tenant-a", business_date=DAY, current_readiness=readiness)
    assert result["items"][0]["status"] == "ambiguous_item"
    assert result["items"][0]["candidate_item_ids"] == ["milk-a", "milk-alt"]
    assert result["locations"][0]["status"] == "ambiguous_location"


def test_duplicate_sheet_cell_fails_closed(db):
    service, _ = db
    row = readiness()["items"][0]
    with pytest.raises(CatinatCountBlocked, match="duplicate_source_count_identity"):
        service.inspect(tenant_id="tenant-a", business_date=DAY,
                        current_readiness=lambda: readiness(row, row))


def test_stale_date_untrusted_source_and_no_readiness_fail_closed(db):
    service, _ = db
    with pytest.raises(CatinatCountBlocked):
        service.inspect(tenant_id="tenant-a", business_date=date(2026, 10, 21),
                        current_readiness=readiness)
    with pytest.raises(CatinatCountBlocked):
        service.inspect(tenant_id="tenant-a", business_date=DAY,
                        current_readiness=lambda: dict(readiness(), mode="signed"))


def _principal(perms):
    return CurrentPrincipal("viewer", "tenant-a", "membership", None,
                            frozenset({"viewer"}), frozenset(perms), False, "session", "test")


def _client_for(principal):
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: principal
    return TestClient(app)


def test_http_only_inventory_read_and_no_write_path(db):
    service, _ = db
    url = "/api/inventory/daily-sheet/catinat/catalog/mapping-preview?business_date=2026-10-20"
    assert _client_for(_principal(set())).get(url).status_code == 403
    client = _client_for(_principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router.CatinatCatalogMappingPreview",
               return_value=service), patch(
        "app.modules.inventory.daily_sheet.router.load_evening_readiness",
        return_value=readiness(),
    ):
        result = client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["candidate_item_ids"] == ["milk-a"]
    assert result.json()["ready_to_sign"] is False
    assert client.post(url).status_code == 405