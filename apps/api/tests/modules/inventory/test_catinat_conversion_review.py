"""Q31: strict unit conversion evidence is read-only and tenant-bound."""
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
from app.modules.inventory.daily.catinat_conversion_review import read_only_conversion_review
from app.modules.inventory.daily.catinat_count_confirmations import CatinatCountBlocked
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryItemModel, InventoryLocationModel, InventoryMaterialPackageConversionModel,
)

DAY = date(2026, 10, 20)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__, InventoryItemModel.__table__, InventoryLocationModel.__table__,
        InventoryMaterialPackageConversionModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as s:
        s.add_all([
            TenantModel(id="tenant-a", name="Catinat", slug="catinat"),
            TenantModel(id="tenant-b", name="Other", slug="other"),
            InventoryItemModel(id="milk-a", tenant_id="tenant-a", sku="milk-a",
                name="SỮA ĐẶC", base_unit="gr", whole_unit="lon", fraction_unit="gr",
                conversion_factor=Decimal("380"), active=True),
            InventoryLocationModel(id="warehouse-a", tenant_id="tenant-a", code="a",
                name="KHO PHA CHẾ", active=True),
            InventoryItemModel(id="milk-b", tenant_id="tenant-b", sku="milk-b",
                name="SỮA ĐẶC", base_unit="gr", whole_unit="lon", fraction_unit="gr",
                conversion_factor=Decimal("380"), active=True),
            InventoryLocationModel(id="warehouse-b", tenant_id="tenant-b", code="b",
                name="KHO PHA CHẾ", active=True),
        ])
    yield sessions
    engine.dispose()


def _sheet(rows=None, sha="a" * 64):
    return {
        "business_date": DAY.isoformat(), "shift": "evening",
        "mode": "read_only_not_a_signed_close", "source_hash": sha,
        "spreadsheet_id": "trusted-workbook-a",
        "items": rows if rows is not None else [{
            "sheet": "KHO PHA CHẾ", "cell": "L5", "material": "SỮA ĐẶC",
            "unit": "lon", "form": "whole", "raw": "2", "quantity": "2",
        }],
    }


def _report(sessions, *, tenant="tenant-a", rows=None, sha="a" * 64):
    return CatinatCatalogMappingPreview(sessions).inspect(
        tenant_id=tenant, business_date=DAY,
        current_readiness=lambda: _sheet(rows, sha),
    )


def _inspect(sessions, *, tenant="tenant-a", rows=None, sha="a" * 64):
    return read_only_conversion_review(
        sessions, tenant_id=tenant, business_date=DAY,
        report=_report(sessions, tenant=tenant, rows=rows, sha=sha),
    )


def _conversion(sessions, *, tenant="tenant-a", item="milk-a",
                factor="380", approved_by="boss-user", canonical_unit="gr"):
    with sessions.begin() as s:
        s.add(InventoryMaterialPackageConversionModel(
            id="conversion-" + tenant, tenant_id=tenant, item_id=item,
            package_name="lon", normalized_package="lon",
            canonical_value=Decimal(factor), canonical_unit=canonical_unit,
            approved_by=approved_by,
        ))


def test_legacy_catalog_factor_never_counts_as_approval(db):
    report = _inspect(db)
    assert report["items"][0]["status"] == "approved_package_conversion_missing"
    assert report["items"][0]["approved_factor_evidence"] is None
    assert report["conversion_applied"] is False
    assert report["ready_to_sign"] is False
    assert report["inventory_writes"] == report["sheets_writes"] == 0


def test_explicit_manager_conversion_is_evidence_only(db):
    _conversion(db)
    report = _inspect(db)
    assert report["items"][0]["status"] == "approved_conversion_evidence_found"
    assert report["items"][0]["approved_factor_evidence"] == "380.00000000"
    assert report["items"][0]["approved_target_unit_evidence"] == "gr"
    assert report["summary"]["approved_conversion_evidence_cells"] == 1
    assert report["automatic_carry_forward_allowed"] is False
    assert report["conversion_applied"] is False


@pytest.mark.parametrize("factor,approver,unit,expected", [
    ("380", None, "gr", "package_conversion_not_approved"),
    ("380", " ", "gr", "package_conversion_not_approved"),
    ("380", "boss-user", "ml", "approved_conversion_unit_or_factor_invalid"),
    ("400", "boss-user", "gr", "approved_conversion_disagrees_with_catalog"),
])
def test_invalid_or_conflicting_conversion_fails_closed(db, factor, approver, unit, expected):
    _conversion(db, factor=factor, approved_by=approver, canonical_unit=unit)
    report = _inspect(db)
    assert report["items"][0]["status"] == expected
    assert report["summary"]["approved_conversion_evidence_cells"] == 0


def test_other_tenant_approval_not_used_for_this_tenant(db):
    _conversion(db, tenant="tenant-b", item="milk-b")
    assert _inspect(db)["items"][0]["status"] == "approved_package_conversion_missing"
    assert _inspect(db, tenant="tenant-b")["items"][0]["status"] == "approved_conversion_evidence_found"


def test_mismatched_source_unit_does_not_infer_conversion(db):
    _conversion(db)
    rows=[{"sheet":"KHO PHA CHẾ","cell":"L5","material":"SỮA ĐẶC",
           "form":"whole","unit":"hộp","raw":"2","quantity":"2"}]
    result=_inspect(db, rows=rows)
    assert result["items"][0]["status"] == "catalog_mapping_requires_review"
    assert result["summary"]["approved_conversion_evidence_cells"] == 0


def test_loose_stock_is_not_a_conversion_approval(db):
    rows=[{"sheet":"KHO PHA CHẾ","cell":"M5","material":"SỮA ĐẶC",
           "form":"loose","unit":"gr","raw":"15","quantity":"15"}]
    report=_inspect(db, rows=rows)
    assert report["items"][0]["status"] == "loose_unit_matches_catalog"
    assert report["summary"]["approved_conversion_evidence_cells"] == 0
    assert report["conversion_applied"] is False


def test_untrusted_or_duplicate_sheet_preview_rejected(db):
    original=_report(db)
    with pytest.raises(CatinatCountBlocked, match="conversion_preview_untrusted"):
        read_only_conversion_review(
            db, tenant_id="tenant-a", business_date=DAY,
            report=dict(original, source_hash="invalid"),
        )
    with pytest.raises(CatinatCountBlocked, match="conversion_preview_duplicate_or_unknown_cell"):
        read_only_conversion_review(
            db, tenant_id="tenant-a", business_date=DAY,
            report=dict(original, items=original["items"] * 2),
        )


def _client(principal):
    app=FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route,"path","").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: principal
    return TestClient(app)


def _principal(perms):
    return CurrentPrincipal("reader","tenant-a","member",None,
                            frozenset({"viewer"}),frozenset(perms),False,"session","test")


def test_http_inventory_read_only_and_no_write_api(db):
    url="/api/inventory/daily-sheet/catinat/catalog-certifications/conversion-review?business_date=2026-10-20"
    assert _client(_principal(set())).get(url).status_code == 403
    client=_client(_principal({"inventory.read"}))
    assert client.post(url).status_code == 405
    with patch(
        "app.modules.inventory.daily.catinat_certification_router.load_evening_readiness",
        return_value=_sheet(),
    ), patch(
        "app.modules.inventory.daily.catinat_certification_router.CatinatCatalogMappingPreview"
    ) as loader, patch(
        "app.modules.inventory.daily.catinat_certification_router.read_only_conversion_review",
        return_value={"conversion_applied":False,"ready_to_sign":False,"sheets_writes":0},
    ):
        loader.return_value.inspect.return_value=_report(db)
        result=client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()["conversion_applied"] is False
