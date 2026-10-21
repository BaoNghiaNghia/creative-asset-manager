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
from app.modules.inventory.daily.catinat_close_preflight import CatinatClosePreflightService
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatCountConfirmationModel,
    InventoryCatinatApprovalRequestModel,
    InventoryCatinatTransferModel,
    InventoryCatinatSignedShiftModel,
    InventoryItemModel, InventoryLocationModel,
    inventory_utcnow,
)

DAY = date(2026, 10, 20)


def ready(hash_="a" * 64):
    return {
        "mode": "read_only_not_a_signed_close", "business_date": DAY.isoformat(),
        "shift": "evening", "spreadsheet_id": "sheet-bound", "source_hash": hash_,
        "items": [
            {"sheet": "KHO PHA CHẾ", "cell": "L5",
             "material": "SỮA ĐẶC", "form": "whole", "unit": "lon",
             "raw": "0", "quantity": "0"},
        ],
        "blockers": [
            {"sheet": "KHO PHA CHẾ", "cell": "L5",
             "code": "employee_physical_confirmation_required"},
            {"code": "prepared_time_and_expiry_require_staff_verification",
             "sheet": "Báo cáo sử dụng NVL trong ca", "row": 22},
        ],
    }


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__,
        InventoryItemModel.__table__, InventoryLocationModel.__table__,
        InventoryCatinatCountConfirmationModel.__table__,
        InventoryCatinatApprovalRequestModel.__table__,
        InventoryCatinatTransferModel.__table__,
        InventoryCatinatSignedShiftModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="Catinat A", slug="catinat-a"))
        session.add(InventoryItemModel(
            id="milk", tenant_id="tenant-a", sku="milk", name="Sữa đặc",
            base_unit="gr", whole_unit="lon", fraction_unit="gr",
            conversion_factor=Decimal("380"), active=True,
        ))
        for id_, code in [("from-bar", "store"), ("to-bar", "bar")]:
            session.add(InventoryLocationModel(
                id=id_, tenant_id="tenant-a", code=code, name=code, active=True,
            ))
    yield CatinatClosePreflightService(sessions), sessions
    engine.dispose()


def codes(result):
    return [row["code"] for row in result["blockers"]]


def test_catalog_coverage_shows_missing_locations_and_never_certifies_by_count(db):
    service, sessions = db
    with sessions.begin() as session:
        for location in session.query(InventoryLocationModel).filter_by(tenant_id="tenant-a"):
            location.active = False
        session.add(TenantModel(id="tenant-b", name="Other shop", slug="other-shop"))
        session.add(InventoryLocationModel(
            id="other-location", tenant_id="tenant-b", code="store",
            name="Other shop warehouse", active=True,
        ))
        session.add(InventoryItemModel(
            id="other-item", tenant_id="tenant-b", sku="other-item",
            name="Other material", base_unit="kg", conversion_factor=Decimal("1"),
            active=True,
        ))
    report = service.inspect(
        tenant_id="tenant-a", business_date=DAY, current_readiness=ready,
    )
    assert report["catalog_coverage"] == {
        "active_item_count": 1,
        "active_location_count": 0,
        "certified": False,
    }
    assert "inventory_locations_missing_for_tenant" in codes(report)
    assert "catalog_item_and_location_mapping_not_certified" in codes(report)
    assert report["ready_to_sign"] is False
    assert report["sheets_writes"] == 0


def test_catalog_coverage_fails_closed_when_items_inactive(db):
    service, sessions = db
    with sessions.begin() as session:
        item = session.get(InventoryItemModel, "milk")
        item.active = False
    report = service.inspect(
        tenant_id="tenant-a", business_date=DAY, current_readiness=ready,
    )
    assert report["catalog_coverage"]["active_item_count"] == 0
    assert report["catalog_coverage"]["active_location_count"] == 2
    assert report["catalog_coverage"]["certified"] is False
    assert "inventory_items_missing_for_tenant" in codes(report)
    assert report["ready_to_sign"] is False


def test_unconfirmed_counts_block_close_without_any_write(db):
    service, _ = db
    output = service.inspect(
        tenant_id="tenant-a", business_date=DAY, current_readiness=ready,
    )
    assert output["confirmed_count"] == 0
    assert output["required_count"] == 1
    assert "physical_count_unconfirmed_or_stale" in codes(output)
    assert "prepared_time_and_expiry_require_staff_verification" in codes(output)
    assert "employee_physical_confirmation_required" not in codes(output)
    assert output["ready_to_sign"] is False
    assert output["automatic_carry_forward_allowed"] is False
    assert output["sheets_writes"] == 0


def test_verified_count_can_be_recognized_without_falsely_closing(db):
    service, sessions = db
    with sessions.begin() as session:
        session.add(InventoryCatinatCountConfirmationModel(
            tenant_id="tenant-a", business_date=DAY, shift="evening",
            spreadsheet_file_id="sheet-bound", sheet_name="KHO PHA CHẾ", cell_ref="L5",
            source_hash="a" * 64, material_display="SỮA ĐẶC", form="whole",
            unit="lon", physical_quantity="0", physical_raw="0",
            confirmed_by="staff-a", confirmed_at=inventory_utcnow(),
        ))
    output = service.inspect(tenant_id="tenant-a", business_date=DAY, current_readiness=ready)
    assert output["confirmed_count"] == 1
    assert "physical_count_unconfirmed_or_stale" not in codes(output)
    assert "catalog_item_and_location_mapping_not_certified" in codes(output)
    assert output["ready_to_sign"] is False
    assert output["official_shift_closed"] is False
    stale = service.inspect(
        tenant_id="tenant-a", business_date=DAY,
        current_readiness=lambda: ready("b" * 64),
    )
    assert stale["confirmed_count"] == 0


def test_pending_transfer_and_manager_approval_evidence_required(db):
    service, sessions = db
    with sessions.begin() as session:
        session.add(InventoryCatinatTransferModel(
            tenant_id="tenant-a", reference_key="handoff-a",
            business_date=DAY, item_id="milk", source_location_id="from-bar",
            destination_location_id="to-bar", unit="lon",
            dispatched_quantity=Decimal(6), received_quantity=Decimal(3),
            status="partial_receipt", dispatched_by="staff-a",
            dispatched_at=inventory_utcnow(), ledger_posted=False,
        ))
        session.add(InventoryCatinatApprovalRequestModel(
            tenant_id="tenant-a", idempotency_key="adjustment-a",
            business_date=DAY, kind="transfer_exception", subject_ref="handoff-a",
            payload_json={"reason": "unreceived"}, proposal_hash="c" * 64,
            reason="Pending handoff at closing", status="approved",
            requested_by="staff-a", decided_by="manager-b",
            decision_reason="Investigate before shift close",
            created_at=inventory_utcnow(), decided_at=inventory_utcnow(),
        ))
    output = service.inspect(tenant_id="tenant-a", business_date=DAY, current_readiness=ready)
    assert output["outstanding_transfer_count"] == 1
    assert output["unreconciled_transfer_count"] == 1
    assert output["approval_requests_count"] == 1
    assert "pending_transfers_require_receipt_or_manager_exception" in codes(output)
    assert "approved_exception_not_yet_applied" in codes(output)
    assert "transfer_ledger_reconciliation_required" in codes(output)
    assert output["ready_to_sign"] is False


def test_signed_shift_changed_source_requires_manager_adjustment(db):
    service, sessions = db
    with sessions.begin() as session:
        session.add(InventoryCatinatSignedShiftModel(
            tenant_id="tenant-a", business_date=DAY, shift="evening",
            source_spreadsheet_file_id="sheet-bound", source_hash="a" * 64,
            snapshot_hash="d" * 64, counts_json=[{"physical_quantity": "0"}],
            confirmed_by="staff-a", confirmed_at=inventory_utcnow(),
            created_at=inventory_utcnow(),
        ))
    changed = service.inspect(
        tenant_id="tenant-a", business_date=DAY,
        current_readiness=lambda: ready("b" * 64),
    )
    assert "post_close_sheet_changed_manager_review_required" in codes(changed)
    assert changed["ready_to_sign"] is False
    assert changed["official_shift_closed"] is True


def principal(permissions):
    return CurrentPrincipal(
        "viewer", "tenant-a", "membership", None, frozenset({"viewer"}),
        frozenset(permissions), False, "session", "test",
    )


def client_for(actor):
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: actor
    return TestClient(app)


def test_http_read_permission_required_and_no_close_action(db):
    service, _ = db
    url = "/api/inventory/daily-sheet/catinat/shift/closing-preflight?business_date=2026-10-20"
    assert client_for(principal(set())).get(url).status_code == 403
    client = client_for(principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router.CatinatClosePreflightService",
               return_value=service), patch(
        "app.modules.inventory.daily_sheet.router.load_evening_readiness",
        return_value=ready(),
    ):
        response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["ready_to_sign"] is False
    assert client.post(url).status_code == 405
