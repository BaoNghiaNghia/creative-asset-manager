from copy import deepcopy
from datetime import date
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
from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, CatinatCountConfirmationService,
)
from app.modules.inventory.daily_sheet.catinat_shift_readiness import (
    serialize_evening_readiness, REPORT_TAB,
)
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import InventoryCatinatCountConfirmationModel

DAY = date(2026, 10, 20)


def _sheet(width=15):
    return [[""] * width for _ in range(7)]


def ready():
    storage = _sheet()
    storage[0][9] = "20/10/2026"
    storage[2][11] = "Tồn cuối"
    storage[4][0] = "BỘT MATCHA"
    storage[4][8] = "gói"
    storage[4][11] = "/"  # Explicit zero, not a confirmation!
    bar = _sheet()
    bar[0][9] = "20/10/2026"
    bar[2][7] = "Tồn cuối"
    bar[4][0] = "SỮA ĐẶC"
    bar[4][8] = "lon"
    bar[4][7] = "6"
    bar[4][10] = "gr"
    roast = _sheet()
    roast[0][11] = "20/10/2026"
    roast[2][9] = "Tồn cuối"
    roast[4][0] = "PIZZA"
    roast[4][10] = "CÁI"
    roast[4][9] = "8"
    report = _sheet(11)
    report[2][1] = "20/10/2026"
    report[4][0] = "STT"
    report[4][1] = "Tên Nguyên Liệu / Vật Tư"
    return serialize_evening_readiness(
        spreadsheet_id="owner-configured-sheet",
        business_date=DAY, ranges={
            "KHO PHA CHẾ": storage, "PHÒNG PHA CHẾ": bar,
            "KHO PHÒNG RANG": roast, REPORT_TAB: report,
        },
    )


@pytest.fixture
def data():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__,
        InventoryCatinatCountConfirmationModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([
            TenantModel(id="tenant-a", name="Catinat A", slug="catinat-a"),
            TenantModel(id="tenant-b", name="Catinat B", slug="catinat-b"),
        ])
    yield CatinatCountConfirmationService(sessions), sessions
    engine.dispose()


def confirm(service, source=None, *, actor="staff-a", cells=None, expected_hash=None, tenant="tenant-a"):
    source = ready() if source is None else source
    return service.confirm(
        tenant_id=tenant, actor_id=actor, business_date=DAY,
        expected_source_hash=expected_hash or source["source_hash"],
        cells=cells or [{"sheet": "KHO PHA CHẾ", "cell": "L5"}],
        current_readiness=lambda: source,
    )


def test_confirmation_records_live_source_zero_and_authenticated_staff(data):
    service, sessions = data
    recorded = confirm(service)
    assert recorded["official_shift_closed"] is False
    assert recorded["inventory_write_performed"] is False
    assert recorded["sheets_writes"] == 0
    item = recorded["confirmed"][0]
    assert item["raw"] == "/"
    assert item["quantity"] == "0"
    assert item["confirmed_by"] == "staff-a"
    with sessions() as session:
        rows = list(session.scalars(select(InventoryCatinatCountConfirmationModel)))
        assert len(rows) == 1
        assert rows[0].spreadsheet_file_id == "owner-configured-sheet"


def test_retries_idempotent_and_other_actor_cannot_take_over(data):
    service, sessions = data
    first = confirm(service)
    second = confirm(service)
    assert second["confirmed"][0]["id"] == first["confirmed"][0]["id"]
    assert second["confirmed"][0]["idempotent_replay"]
    with pytest.raises(CatinatCountBlocked, match="physical_cell_already_confirmed_by_other"):
        confirm(service, actor="staff-b")
    with sessions() as session:
        assert len(list(session.scalars(select(InventoryCatinatCountConfirmationModel)))) == 1


def test_stale_or_changed_data_cannot_reuse_earlier_confirmation(data):
    service, sessions = data
    original = ready()
    first = confirm(service, original)
    changed = deepcopy(original)
    changed["source_hash"] = "b" * 64
    for item in changed["items"]:
        if item["cell"] == "L5":
            item["raw"] = "1"
            item["quantity"] = "1"
    with pytest.raises(CatinatCountBlocked, match="google_sheet_changed_reconfirm_required"):
        confirm(service, changed, expected_hash=original["source_hash"])
    state = service.state(tenant_id="tenant-a", business_date=DAY,
                          current_readiness=lambda: changed)
    assert state["confirmed_count"] == 0
    assert not state["automatic_carry_forward_allowed"]
    rechecked = confirm(service, changed)
    assert rechecked["confirmed"][0]["id"] != first["confirmed"][0]["id"]
    assert service.state(tenant_id="tenant-a", business_date=DAY,
                         current_readiness=lambda: changed)["confirmed_count"] == 1
    with sessions() as session:
        assert len(list(session.scalars(select(InventoryCatinatCountConfirmationModel)))) == 2


def test_confirmation_is_tenant_scoped(data):
    service, _sessions = data
    confirm(service, tenant="tenant-a")
    assert service.state(tenant_id="tenant-b", business_date=DAY,
                         current_readiness=ready)["confirmed_count"] == 0


def test_unverified_raw_and_unknown_cells_block_entire_batch(data):
    service, sessions = data
    for cells in (
        [{"sheet": "PHÒNG PHA CHẾ", "cell": "J5"}],  # unit but no value
        [{"sheet": "KHO PHA CHẾ", "cell": "L5"}, {"sheet": "PHÒNG PHA CHẾ", "cell": "J5"}],
        [{"sheet": "KHO PHA CHẾ", "cell": "L999"}],
        [{"sheet": "UNAUTHORIZED", "cell": "H5"}],
    ):
        with pytest.raises(CatinatCountBlocked):
            confirm(service, cells=cells)
    with sessions() as session:
        assert not list(session.scalars(select(InventoryCatinatCountConfirmationModel)))


def test_duplicate_or_empty_batch_rejected(data):
    service, _sessions = data
    with pytest.raises(CatinatCountBlocked, match="duplicate_physical_cells"):
        confirm(service, cells=[{"sheet":"KHO PHA CHẾ","cell":"L5"}] * 2)
    with pytest.raises(CatinatCountBlocked, match="invalid_confirmation_batch_size"):
        service.confirm(tenant_id="tenant-a",actor_id="staff-a",business_date=DAY,
                        expected_source_hash=ready()["source_hash"],cells=[],
                        current_readiness=ready)


def test_multi_cell_confirmation_single_transaction(data):
    service, sessions = data
    result=confirm(service,cells=[
        {"sheet":"KHO PHA CHẾ","cell":"L5"},
        {"sheet":"PHÒNG PHA CHẾ","cell":"H5"},
        {"sheet":"KHO PHÒNG RANG","cell":"J5"},
    ])
    assert len(result["confirmed"])==3
    state=service.state(tenant_id="tenant-a",business_date=DAY,current_readiness=ready)
    assert state["confirmed_count"]==3
    assert state["required_count"]>=4
    with sessions() as session:
        assert len(list(session.scalars(select(InventoryCatinatCountConfirmationModel))))==3


def principal(user, roles, permissions, tenant="tenant-a"):
    return CurrentPrincipal(
        user,tenant,"membership",None,frozenset(roles),frozenset(permissions),
        False,"session","test",
    )


def client_for(actor):
    app=FastAPI()
    app.include_router(router,prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call]=lambda:actor
    return TestClient(app)


ENDPOINT="/api/inventory/daily-sheet/catinat/shift/count-confirmations"


def test_http_auth_and_staff_role_required(data):
    service,_sessions=data
    staff=client_for(principal("staff-a",{"operator"},{"inventory.review","inventory.read"}))
    view=client_for(principal("viewer",{"viewer"},{"inventory.read"}))
    odd=client_for(principal("other",{"viewer"},{"inventory.review","inventory.read"}))
    payload={"business_date":DAY.isoformat(),"expected_source_hash":ready()["source_hash"],
             "cells":[{"sheet":"KHO PHA CHẾ","cell":"L5"}]}
    with patch("app.modules.inventory.daily.catinat_count_router._service",return_value=service), patch(
        "app.modules.inventory.daily.catinat_count_router._current",return_value=ready()
    ):
        assert view.post(ENDPOINT,json=payload).status_code==403
        assert odd.post(ENDPOINT,json=payload).status_code==403
        response=staff.post(ENDPOINT,json=payload)
        assert response.status_code==200,response.text
        assert response.json()["confirmed"][0]["confirmed_by"]=="staff-a"
        found=view.get(ENDPOINT+"?business_date=2026-10-20")
        assert found.status_code==200
        assert found.json()["confirmed_count"]==1
        changed=deepcopy(ready())
        changed["source_hash"]="c"*64
        with patch("app.modules.inventory.daily.catinat_count_router._current",return_value=changed):
            conflict=staff.post(ENDPOINT,json=payload)
            assert conflict.status_code==409
            assert conflict.json()["detail"]["code"]=="google_sheet_changed_reconfirm_required"
