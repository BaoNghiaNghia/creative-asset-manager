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
from app.modules.inventory.daily.catinat_approvals import (
    CatinatApprovalBlocked, CatinatApprovalService,
)
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalEventModel, InventoryCatinatApprovalRequestModel,
)

DAY = date(2026, 10, 20)
KEY = "receipt-discrepancy-20261020-01"
PROPOSAL = {"item": "milk", "location": "bar", "expected": 8, "counted": 6}
REQUEST_REASON = "Staff counted two cans fewer than expected"
DECISION_REASON = "Reviewed signed count and discrepancy evidence"


@pytest.fixture
def backend():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__,
        InventoryCatinatApprovalRequestModel.__table__,
        InventoryCatinatApprovalEventModel.__table__,
    ])
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        db.add(TenantModel(id="tenant-a", name="Catinat A", slug="catinat-a"))
        db.add(TenantModel(id="tenant-b", name="Catinat B", slug="catinat-b"))
    yield CatinatApprovalService(sessions), sessions
    engine.dispose()


def submit(svc, **overrides):
    args = dict(
        tenant_id="tenant-a", actor_id="staff-user",
        idempotency_key=KEY, business_date=DAY, kind="discrepancy",
        subject_ref="bar:milk", proposal=PROPOSAL, reason=REQUEST_REASON,
    )
    args.update(overrides)
    return svc.request(**args)


def resolve(svc, request_id, **overrides):
    args = dict(
        tenant_id="tenant-a", actor_id="boss-user", request_id=request_id,
        decision="approved", reason=DECISION_REASON,
    )
    args.update(overrides)
    return svc.decide(**args)


def events(sessions):
    with sessions() as db:
        return list(db.scalars(
            select(InventoryCatinatApprovalEventModel)
            .order_by(InventoryCatinatApprovalEventModel.created_at)
        ))


def test_request_creates_one_immutable_proposal_and_append_only_event(backend):
    svc, sessions = backend
    first = submit(svc)
    assert first["status"] == "pending"
    assert first["inventory_write_performed"] is False
    assert first["requested_by"] == "staff-user"
    again = submit(svc)
    assert first["id"] == again["id"]
    assert again["idempotent_replay"] is True
    with sessions() as db:
        assert len(list(db.scalars(select(InventoryCatinatApprovalRequestModel)))) == 1
    assert [event.action for event in events(sessions)] == ["requested"]


def test_duplicate_idempotency_key_cannot_change_proposal_or_owner(backend):
    svc, _ = backend
    submit(svc)
    with pytest.raises(CatinatApprovalBlocked, match="approval_idempotency_conflict"):
        submit(svc, proposal={"expected": 0})
    with pytest.raises(CatinatApprovalBlocked, match="approval_idempotency_conflict"):
        submit(svc, actor_id="other-user")


def test_manager_can_approve_only_someone_elses_request_and_only_once(backend):
    svc, sessions = backend
    request = submit(svc)
    with pytest.raises(CatinatApprovalBlocked, match="self_approval_forbidden"):
        resolve(svc, request["id"], actor_id="staff-user")
    decision = resolve(svc, request["id"])
    assert decision["status"] == "approved"
    assert decision["decided_by"] == "boss-user"
    assert decision["inventory_write_performed"] is False
    assert resolve(svc, request["id"])["idempotent_replay"] is True
    assert [row.action for row in events(sessions)] == ["requested", "approved"]
    with pytest.raises(CatinatApprovalBlocked, match="approval_already_decided"):
        resolve(svc, request["id"], actor_id="other-boss")
    with pytest.raises(CatinatApprovalBlocked, match="approval_already_decided"):
        resolve(svc, request["id"], decision="rejected")


def test_rejection_immutable_and_events_audited(backend):
    svc, sessions = backend
    request = submit(svc)
    rejected = resolve(svc, request["id"], decision="rejected")
    assert rejected["status"] == "rejected"
    assert [row.action for row in events(sessions)] == ["requested", "rejected"]
    with pytest.raises(CatinatApprovalBlocked, match="approval_already_decided"):
        resolve(svc, request["id"], decision="approved")


def test_tenant_scoped_read_and_decision(backend):
    svc, _ = backend
    request = submit(svc)
    with pytest.raises(CatinatApprovalBlocked, match="approval_not_found"):
        svc.get(tenant_id="tenant-b", request_id=request["id"])
    with pytest.raises(CatinatApprovalBlocked, match="approval_not_found"):
        resolve(svc, request["id"], tenant_id="tenant-b")
    assert svc.list(tenant_id="tenant-b") == []


def test_invalid_kind_proposal_and_reason_rejected(backend):
    svc, _ = backend
    for overrides in (
        {"kind": "expiry_exception"},
        {"proposal": {}},
        {"reason": "short"},
        {"idempotency_key": "a"},
        {"proposal": {"lots": list(range(10000))}},
        {"proposal": {"float": float("nan")}},
    ):
        with pytest.raises(CatinatApprovalBlocked):
            submit(svc, **overrides)


def test_pending_filter_and_cross_tenant_idempotency(backend):
    svc, _ = backend
    a = submit(svc)
    b = submit(svc, tenant_id="tenant-b", actor_id="staff-b")
    assert a["id"] != b["id"]
    resolve(svc, a["id"])
    assert len(svc.list(tenant_id="tenant-a", status="approved")) == 1
    assert svc.list(tenant_id="tenant-a", status="pending") == []
    assert len(svc.list(tenant_id="tenant-b", status="pending")) == 1


def principal(user, permissions, roles, tenant="tenant-a"):
    return CurrentPrincipal(
        user, tenant, f"m-{user}", None, frozenset(roles),
        frozenset(permissions), False, f"s-{user}", "test",
    )


def client_for(actor):
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for subdep in dep.dependencies:
                    app.dependency_overrides[subdep.call] = lambda: actor
    return TestClient(app)


def data():
    return dict(
        idempotency_key=KEY, business_date=DAY.isoformat(),
        kind="discrepancy", subject_ref="bar:milk",
        proposal=PROPOSAL, reason=REQUEST_REASON,
    )


API = "/api/inventory/daily-sheet/catinat/approvals"


def test_http_review_and_manager_role_separation(backend):
    svc, _ = backend
    employee = client_for(principal("staff-user", {"inventory.review", "inventory.read"}, {"operator"}))
    nonmanager = client_for(principal("other-user", {"inventory.finalize", "inventory.read"}, {"operator"}))
    manager = client_for(principal("boss-user", {"inventory.finalize", "inventory.read"}, {"tenant_admin"}))
    read_only = client_for(principal("reader", {"inventory.read"}, {"viewer"}))
    with patch("app.modules.inventory.daily.catinat_approval_router._service", return_value=svc):
        forbidden = read_only.post(API, json=data())
        assert forbidden.status_code == 403
        created = employee.post(API, json=data())
        assert created.status_code == 200, created.text
        request_id = created.json()["id"]
        assert employee.get(API).json()["items"][0]["id"] == request_id
        assert nonmanager.post(f"{API}/{request_id}/decision",
                               json={"decision": "approved", "reason": DECISION_REASON}).status_code == 403
        approved = manager.post(
            f"{API}/{request_id}/decision",
            json={"decision": "approved", "reason": DECISION_REASON},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert approved.json()["inventory_write_performed"] is False
        assert manager.get(f"{API}/{request_id}").status_code == 200


def test_http_manager_cannot_self_approve_or_cross_tenant(backend):
    svc, _ = backend
    manager = client_for(principal(
        "boss-user", {"inventory.control", "inventory.finalize", "inventory.read"},
        {"tenant_admin"},
    ))
    other_tenant_manager = client_for(principal(
        "boss-elsewhere", {"inventory.finalize", "inventory.read"},
        {"tenant_admin"}, tenant="tenant-b",
    ))
    with patch("app.modules.inventory.daily.catinat_approval_router._service", return_value=svc):
        created = manager.post(API, json=data())
        assert created.status_code == 200
        rid = created.json()["id"]
        self_decision = manager.post(
            f"{API}/{rid}/decision", json={"decision": "approved", "reason": DECISION_REASON},
        )
        assert self_decision.status_code == 403
        assert self_decision.json()["detail"]["code"] == "self_approval_forbidden"
        assert other_tenant_manager.get(f"{API}/{rid}").status_code == 404
        assert other_tenant_manager.post(
            f"{API}/{rid}/decision", json={"decision": "approved", "reason": DECISION_REASON},
        ).status_code == 404
