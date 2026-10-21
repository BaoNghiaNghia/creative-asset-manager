"""Catinat expiry / disposal / transfer triage is deliberately not stock posting."""
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
from app.modules.inventory.daily.catinat_count_confirmations import CatinatCountBlocked
from app.modules.inventory.daily.catinat_safety_audit import read_only_shelf_life_transfer_audit
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatTransferModel, InventoryItemModel, InventoryLocationModel,
    inventory_utcnow,
)

DAY = date(2026, 10, 20)


def source(sha="a" * 64):
    return {
        "mode": "read_only_not_a_signed_close", "business_date": DAY.isoformat(),
        "shift": "evening", "source_hash": sha, "spreadsheet_id": "tenant-sheet",
        "items": [
            {"sheet": "KHO PHA CHẾ", "cell": "L5", "material": "Sữa đặc",
             "unit": "lon", "form": "whole", "raw": "0", "quantity": "0"},
        ],
        "blockers": [
            {"code": "prepared_time_and_expiry_require_staff_verification",
             "sheet": "Báo cáo sử dụng NVL trong ca", "row": 22},
            {"code": "employee_physical_confirmation_required",
             "sheet": "KHO PHA CHẾ", "cell": "L5"},
        ],
    }


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__, InventoryItemModel.__table__,
        InventoryLocationModel.__table__, InventoryCatinatTransferModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as s:
        for tenant in ("tenant-a", "tenant-b"):
            s.add(TenantModel(id=tenant, name=tenant, slug=tenant))
            s.add(InventoryItemModel(id="milk-" + tenant, tenant_id=tenant,
                sku="milk", name="Sữa đặc", base_unit="gr", whole_unit="lon",
                conversion_factor=Decimal(380), active=True))
            s.add(InventoryLocationModel(id="from-" + tenant,
                tenant_id=tenant, code="from", name="From", active=True))
            s.add(InventoryLocationModel(id="to-" + tenant,
                tenant_id=tenant, code="to", name="To", active=True))
    yield sessions
    engine.dispose()


def transfer(sessions, tenant="tenant-a", *, sent="6", received="3",
             status="partial_receipt", posted=False, day=DAY):
    with sessions.begin() as s:
        s.add(InventoryCatinatTransferModel(
            tenant_id=tenant, reference_key="reference-" + tenant,
            business_date=day, item_id="milk-" + tenant,
            source_location_id="from-" + tenant,
            destination_location_id="to-" + tenant, unit="lon",
            dispatched_quantity=Decimal(sent), received_quantity=Decimal(received),
            status=status, dispatched_by="staff-a", dispatched_at=inventory_utcnow(),
            ledger_posted=posted,
        ))


def inspect(db, *, tenant="tenant-a", reader=source):
    return read_only_shelf_life_transfer_audit(
        db, tenant_id=tenant, business_date=DAY, current_readiness=reader,
    )


def test_unknown_batch_and_date_only_preparation_never_marked_safe(db):
    report = inspect(db)
    assert report["summary"]["physical_cells_without_batch_expiry_certification"] == 1
    assert report["summary"]["date_only_preparation_hints"] == 1
    assert report["items"][0]["batch_expiry_status"] == "batch_identity_and_exact_time_required"
    assert report["items"][0]["disposal_status"] == "physical_disposal_evidence_not_verified"
    assert report["prepared_report_hints"] == [
        {"sheet": "Báo cáo sử dụng NVL trong ca", "row": 22,
         "status": "prepared_time_unverified_no_expiry_claim"}]
    assert report["batch_expiration_and_waste_certified"] is False
    assert report["transfer_ledger_reconciled"] is False
    assert report["ready_to_sign"] is False
    assert report["inventory_writes"] == report["sheets_writes"] == 0


def test_partial_transfer_keeps_remaining_quantity_unposted(db):
    transfer(db)
    report = inspect(db)
    assert report["summary"]["pending_receipt_transfers"] == 1
    assert report["summary"]["unposted_ledger_transfers"] == 1
    assert report["transfers"][0]["remaining_in_transit"] == "3.00000000"
    assert report["transfers"][0]["requires_receipt"] is True
    assert report["transfers"][0]["requires_ledger_reconciliation"] is True
    assert report["transfers"][0]["stock_posted_by_this_audit"] is False


def test_full_receipt_does_not_prove_ledger_posting(db):
    transfer(db, received="6", status="received")
    report = inspect(db)
    assert report["summary"]["pending_receipt_transfers"] == 0
    assert report["summary"]["unposted_ledger_transfers"] == 1
    assert report["transfers"][0]["requires_ledger_reconciliation"] is True


def test_other_tenant_transfers_not_exposed(db):
    transfer(db, tenant="tenant-b")
    assert inspect(db, tenant="tenant-a")["transfers"] == []
    assert len(inspect(db, tenant="tenant-b")["transfers"]) == 1


@pytest.mark.parametrize("changes,code", [
    ({"business_date": "2026-10-21"}, "source_shift_date_unverifiable"),
    ({"source_hash": "wrong"}, "unverified_google_sheet_source"),
    ({"mode": "officially_closed"}, "untrusted_readiness_source"),
    ({"blockers": None}, "unverifiable_shelf_life_blockers"),
])
def test_untrusted_source_fails_closed(db, changes, code):
    with pytest.raises(CatinatCountBlocked, match=code):
        inspect(db, reader=lambda: dict(source(), **changes))


def test_duplicate_physical_cell_and_date_hint_fail_closed(db):
    original = source()
    with pytest.raises(CatinatCountBlocked, match="duplicate_source_count_identity"):
        inspect(db, reader=lambda: dict(original, items=original["items"] * 2))
    with pytest.raises(CatinatCountBlocked, match="duplicate_prepared_report_evidence"):
        inspect(db, reader=lambda: dict(original, blockers=original["blockers"] * 2))


def test_conflicting_transfer_status_fails_closed(db):
    transfer(db, received="3", status="received")
    with pytest.raises(CatinatCountBlocked, match="transfer_state_quantity_conflict"):
        inspect(db)


def _client(permissions):
    actor = CurrentPrincipal(
        "user", "tenant-a", "member", None, frozenset({"viewer"}),
        frozenset(permissions), False, "session", "test",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: actor
    return TestClient(app)


def test_get_requires_inventory_read_and_no_writer_exists(db):
    url = "/api/inventory/daily-sheet/catinat/shift/safety-triage?business_date=2026-10-20"
    assert _client(set()).get(url).status_code == 403
    client = _client({"inventory.read"})
    assert client.post(url).status_code == 405
    with patch("app.modules.inventory.daily_sheet.router.load_evening_readiness",
               return_value=source()), patch(
        "app.modules.inventory.daily_sheet.router.read_only_shelf_life_transfer_audit",
        return_value={"ready_to_sign": False, "sheets_writes": 0},
    ):
        result = client.get(url)
    assert result.status_code == 200, result.text
    assert result.json()["ready_to_sign"] is False
