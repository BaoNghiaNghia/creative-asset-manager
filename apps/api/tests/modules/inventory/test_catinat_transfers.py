from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory.daily.catinat_transfers import (
    CatinatTransferBlocked, CatinatTransferService,
)
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatTransferModel, InventoryCatinatTransferEventModel,
    InventoryItemModel, InventoryLocationModel,
)

DAY = date(2026, 10, 20)
SRC, DEST, ITEM = "from-bar", "to-bar", "milk-a"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__, InventoryItemModel.__table__,
        InventoryLocationModel.__table__, InventoryCatinatTransferModel.__table__,
        InventoryCatinatTransferEventModel.__table__,
    ])
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all([
            TenantModel(id="tenant-a", name="A", slug="catinat-a"),
            TenantModel(id="tenant-b", name="B", slug="catinat-b"),
        ])
        session.add(InventoryItemModel(
            id=ITEM, tenant_id="tenant-a", sku="milk-a", name="Sữa đặc",
            base_unit="gr", whole_unit="lon", fraction_unit="gr",
            conversion_factor=Decimal("380"), active=True,
        ))
        session.add_all([
            InventoryLocationModel(id=SRC, tenant_id="tenant-a",
                                   code="store", name="Kho pha chế", active=True),
            InventoryLocationModel(id=DEST, tenant_id="tenant-a",
                                   code="bar", name="Phòng pha chế", active=True),
        ])
    yield CatinatTransferService(factory), factory
    engine.dispose()


def dispatch(service, **kwargs):
    args = dict(
        tenant_id="tenant-a", actor_id="sender", reference_key="dispatch-20261020-a",
        business_date=DAY, item_id=ITEM, source_location_id=SRC,
        destination_location_id=DEST, unit="lon", quantity="6",
    )
    args.update(kwargs)
    return service.dispatch(**args)


def receive(service, transfer_id, **kwargs):
    args = dict(
        tenant_id="tenant-a", actor_id="receiver", transfer_id=transfer_id,
        event_key="receipt-20261020-1", quantity="3",
    )
    args.update(kwargs)
    return service.receive(**args)


def test_dispatch_is_pending_no_destination_stock_credit(db):
    svc, sessions = db
    result = dispatch(svc)
    assert result["status"] == "pending_receipt"
    assert Decimal(result["dispatched_quantity"]) == 6
    assert Decimal(result["in_transit_quantity"]) == 6
    assert Decimal(result["received_quantity"]) == 0
    assert result["ledger_posted"] is False
    assert result["inventory_write_performed"] is False
    assert result["sheets_writes"] == 0
    with sessions() as session:
        events = list(session.scalars(select(InventoryCatinatTransferEventModel)))
        assert len(events) == 1 and events[0].action == "dispatched"
        assert not any(event.action == "received" for event in events)


def test_confirmed_receipt_moves_only_received_quantity(db):
    svc, sessions = db
    dispatched = dispatch(svc)
    first = receive(svc, dispatched["id"])
    assert first["status"] == "partial_receipt"
    assert Decimal(first["received_quantity"]) == 3
    assert Decimal(first["in_transit_quantity"]) == 3
    assert first["ledger_posted"] is False
    assert len(svc.list(tenant_id="tenant-a", pending_only=True)) == 1
    second = receive(
        svc, dispatched["id"], event_key="receipt-20261020-2", quantity="3",
    )
    assert second["status"] == "received"
    assert Decimal(second["in_transit_quantity"]) == 0
    assert not svc.list(tenant_id="tenant-a", pending_only=True)
    with sessions() as session:
        actions = [event.action for event in session.scalars(
            select(InventoryCatinatTransferEventModel).order_by(
                InventoryCatinatTransferEventModel.created_at
            )
        )]
        assert actions == ["dispatched", "received", "received"]


def test_duplicate_dispatch_and_duplicate_receipt_idempotent(db):
    svc, sessions = db
    first = dispatch(svc)
    again = dispatch(svc)
    assert again["id"] == first["id"]
    assert again["idempotent_replay"]
    receipt = receive(svc, first["id"])
    replay = receive(svc, first["id"])
    assert replay["idempotent_replay"] is True
    assert replay["received_quantity"] == receipt["received_quantity"]
    with sessions() as session:
        assert len(list(session.scalars(select(InventoryCatinatTransferEventModel)))) == 2


def test_conflicting_retries_cannot_change_outstanding_qty(db):
    svc, _ = db
    dispatched = dispatch(svc)
    with pytest.raises(CatinatTransferBlocked, match="transfer_idempotency_conflict"):
        dispatch(svc, quantity="8")
    receive(svc, dispatched["id"])
    with pytest.raises(CatinatTransferBlocked, match="transfer_event_idempotency_conflict"):
        receive(svc, dispatched["id"], quantity="2")
    with pytest.raises(CatinatTransferBlocked, match="transfer_event_idempotency_conflict"):
        receive(svc, dispatched["id"], actor_id="another")
    with pytest.raises(CatinatTransferBlocked, match="receipt_exceeds_in_transit"):
        receive(svc, dispatched["id"], event_key="receipt-20261020-over", quantity="4")
    assert Decimal(svc.get(tenant_id="tenant-a", transfer_id=dispatched["id"])["in_transit_quantity"]) == 3


def test_cross_tenant_cannot_read_or_receive(db):
    svc, _ = db
    dispatched = dispatch(svc)
    with pytest.raises(CatinatTransferBlocked, match="transfer_not_found"):
        svc.get(tenant_id="tenant-b", transfer_id=dispatched["id"])
    with pytest.raises(CatinatTransferBlocked, match="transfer_not_found"):
        receive(svc, dispatched["id"], tenant_id="tenant-b")
    assert svc.list(tenant_id="tenant-b") == []


@pytest.mark.parametrize("change,reason", [
    ({"item_id": "unknown"}, "approved_active_material_required"),
    ({"source_location_id": DEST}, "transfer_same_location"),
    ({"destination_location_id": "unknown"}, "approved_active_locations_required"),
    ({"unit": "hộp"}, "material_unit_mismatch"),
    ({"quantity": "/"},"invalid_transfer_quantity"),
    ({"quantity": "0"},"invalid_transfer_quantity"),
    ({"quantity": "0.000000001"},"invalid_transfer_quantity"),
    ({"quantity": "-1"},"invalid_transfer_quantity"),
])
def test_unsafe_dispatch_rejected(db, change, reason):
    svc, _ = db
    with pytest.raises(CatinatTransferBlocked, match=reason):
        dispatch(svc, **change)


def test_time_of_receiver_confirmation_not_confused_with_business_date(db):
    svc, _ = db
    dispatched = dispatch(svc)
    result = receive(svc, dispatched["id"])
    assert result["business_date"] == DAY.isoformat()
    assert result["received_last_at"]
    assert result["dispatched_at"]


def principal(user, roles, permissions, tenant="tenant-a"):
    return CurrentPrincipal(
        user, tenant, "membership", None, frozenset(roles),
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


API = "/api/inventory/daily-sheet/catinat/transfers"
BODY = {
    "reference_key": "dispatch-20261020-a", "business_date": "2026-10-20",
    "item_id": ITEM, "source_location_id": SRC,
    "destination_location_id": DEST, "unit": "lon", "quantity": "6",
}


def test_http_staff_role_and_receive_confirmation(db):
    svc, _ = db
    staff = client_for(principal("sender", {"operator"}, {"inventory.review", "inventory.read"}))
    receiver = client_for(principal("receiver", {"operator"}, {"inventory.review", "inventory.read"}))
    only_read = client_for(principal("observer", {"viewer"}, {"inventory.read"}))
    with patch("app.modules.inventory.daily.catinat_transfer_router._service", return_value=svc):
        assert only_read.post(API, json=BODY).status_code == 403
        created = staff.post(API, json=BODY)
        assert created.status_code == 200, created.text
        id_ = created.json()["id"]
        assert only_read.get(API + "?pending_only=true").json()["items"][0]["id"] == id_
        answer = receiver.post(f"{API}/{id_}/receive", json={
            "event_key": "receipt-20261020-1", "quantity": "3",
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["status"] == "partial_receipt"
        assert answer.json()["ledger_posted"] is False
        assert only_read.get(f"{API}/{id_}").status_code == 200


def test_http_cross_tenant_scoping_and_invalid_receiver(db):
    svc, _ = db
    other = client_for(principal(
        "outsider", {"operator"}, {"inventory.review", "inventory.read"},
        tenant="tenant-b",
    ))
    staff = client_for(principal("sender", {"operator"}, {"inventory.review", "inventory.read"}))
    with patch("app.modules.inventory.daily.catinat_transfer_router._service", return_value=svc):
        dispatched = staff.post(API, json=BODY).json()
        assert other.get(f"{API}/{dispatched['id']}").status_code == 404
        assert other.post(f"{API}/{dispatched['id']}/receive", json={
            "event_key": "receipt-20261020-1", "quantity": "3",
        }).status_code == 404
