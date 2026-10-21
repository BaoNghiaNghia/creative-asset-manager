"""Q40/Q45 batch evidence is human-attested, append-only, never stock disposal."""
from datetime import date, datetime, timedelta, timezone
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
from app.modules.inventory.daily.catinat_approvals import CatinatApprovalService
from app.modules.inventory.daily.catinat_batch_evidence import (
    CatinatBatchBlocked, CatinatBatchEvidenceService,
)
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalRequestModel, InventoryCatinatApprovalEventModel,
    InventoryCatinatBatchEvidenceModel,
    InventoryCatinatWasteWitnessModel, InventoryCatinatWasteCertificationModel,
)

DAY = date(2026, 10, 20)
EVENT = datetime(2026, 10, 19, 14, 30, tzinfo=timezone.utc)


def source(sha="a" * 64):
    return {
        "mode": "read_only_not_a_signed_close",
        "business_date": DAY.isoformat(), "shift": "evening",
        "source_hash": sha, "spreadsheet_id": "catinat-trusted-sheet",
        "items": [
            {"sheet": "KHO PHA CHẾ", "cell": "L5", "material": "Sữa đặc",
             "form": "whole", "unit": "lon", "raw": "2", "quantity": "2"},
        ],
    }


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__,
        InventoryCatinatApprovalRequestModel.__table__,
        InventoryCatinatApprovalEventModel.__table__,
        InventoryCatinatBatchEvidenceModel.__table__,
        InventoryCatinatWasteWitnessModel.__table__, InventoryCatinatWasteCertificationModel.__table__,
    ])
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as s:
        s.add_all([
            TenantModel(id="tenant-a", name="Catinat", slug="catinat"),
            TenantModel(id="tenant-b", name="Other shop", slug="other"),
        ])
    yield CatinatBatchEvidenceService(sessions), sessions
    engine.dispose()


def record(service, **overrides):
    values = {
        "tenant_id": "tenant-a", "actor_id": "staff-a",
        "idempotency_key": "batch-key-1234", "business_date": DAY,
        "expected_source_hash": "a" * 64, "sheet": "KHO PHA CHẾ",
        "cell": "L5", "lot_reference": "LOT-1920",
        "event_kind": "prepared", "event_at": EVENT,
        "package_expiry_at": None,
        "note": "Nhân viên trực tiếp xác nhận thời điểm",
        "current_readiness": source,
    }
    return service.record(**dict(values, **overrides))


def request_disposal(service, batch_id, **overrides):
    values = {
        "tenant_id": "tenant-a", "actor_id": "staff-a",
        "batch_id": batch_id, "business_date": DAY,
        "expected_source_hash": "a" * 64,
        "idempotency_key": "waste-review-key-123",
        "quantity": "1.5",
        "reason": "Yêu cầu quản lý kiểm tra lô nghi quá hạn trước khi hủy",
        "current_readiness": source,
    }
    return service.request_disposal(**dict(values, **overrides))


def test_human_attestation_is_immutable_and_not_expiry_certification(db):
    service, sessions = db
    item = record(service)
    assert item["material"] == "Sữa đặc"
    assert item["status"] == "staff_attested_unverified"
    assert item["expiry_certified"] is False
    assert item["disposal_performed"] is False
    assert item["ready_to_sign"] is False
    assert item["inventory_writes"] == item["sheets_writes"] == 0
    result = service.list(tenant_id="tenant-a", business_date=DAY,
                          current_readiness=source)
    assert result["source_hash"] == "a" * 64
    assert len(result["items"]) == 1
    with sessions() as session:
        assert session.query(InventoryCatinatBatchEvidenceModel).count() == 1


def test_exact_idempotency_replay_but_changed_input_conflicts(db):
    service, sessions = db
    a = record(service)
    b = record(service)
    assert b["id"] == a["id"]
    assert b["idempotent_replay"] is True
    with pytest.raises(CatinatBatchBlocked, match="batch_evidence_idempotency_conflict"):
        record(service, lot_reference="OTHER-LOT")
    with pytest.raises(CatinatBatchBlocked, match="batch_evidence_idempotency_conflict"):
        record(service, actor_id="other-staff")
    with sessions() as session:
        assert session.query(InventoryCatinatBatchEvidenceModel).count() == 1


def test_stale_hash_and_ambiguous_source_fail_closed(db):
    service, _ = db
    with pytest.raises(CatinatBatchBlocked, match="google_sheet_changed_reconfirm_required"):
        record(service, current_readiness=lambda: source("b" * 64))
    rows = source()
    rows["items"] = [rows["items"][0], rows["items"][0]]
    with pytest.raises(CatinatBatchBlocked, match="source_cell_not_unique_or_missing"):
        record(service, current_readiness=lambda: rows)
    with pytest.raises(CatinatBatchBlocked, match="source_cell_not_unique_or_missing"):
        record(service, cell="L6")


def test_no_unknown_or_naive_timestamp_and_no_future_events(db):
    service, _ = db
    with pytest.raises(CatinatBatchBlocked, match="exact_timezone_aware_timestamp_required"):
        record(service, event_at=EVENT.replace(tzinfo=None))
    with pytest.raises(CatinatBatchBlocked, match="event_time_cannot_be_in_future"):
        record(service, event_at=datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(CatinatBatchBlocked, match="exact_timezone_aware_timestamp_required"):
        record(service, package_expiry_at=EVENT.replace(tzinfo=None))


def test_opened_package_is_unverified_without_product_policy(db):
    service, _ = db
    item = record(service, event_kind="opened",
                  package_expiry_at=EVENT + timedelta(days=20))
    assert item["event_kind"] == "opened"
    assert item["package_expiry_at"] is not None
    assert item["expiry_certified"] is False


def test_other_tenant_cannot_read_or_use_batch(db):
    service, _ = db
    batch = record(service)
    result = service.list(tenant_id="tenant-b", business_date=DAY,
                          current_readiness=source)
    assert result["items"] == []
    with pytest.raises(CatinatBatchBlocked, match="batch_evidence_not_found"):
        request_disposal(service, batch["id"], tenant_id="tenant-b")


def test_disposal_request_uses_manager_queue_and_does_not_consume_stock(db):
    service, sessions = db
    batch = record(service)
    review = request_disposal(service, batch["id"])
    assert review["kind"] == "waste_disposal"
    assert review["status"] == "pending"
    assert review["inventory_write_performed"] is False
    assert review["proposal"]["disposal_physically_verified"] is False
    assert review["proposal"]["ledger_posted"] is False
    assert review["proposal"]["sheets_writes"] == 0
    assert review["proposal"]["quantity_requested"] == "1.5"
    # The existing manager approval remains an audit decision only.
    approved = CatinatApprovalService(sessions).decide(
        tenant_id="tenant-a", actor_id="manager-b", request_id=review["id"],
        decision="approved", reason="Đã nhận yêu cầu, cần chứng từ hủy riêng",
    )
    assert approved["status"] == "approved"
    assert approved["inventory_write_performed"] is False
    assert approved["proposal"]["ledger_posted"] is False
    with sessions() as session:
        row = session.get(InventoryCatinatBatchEvidenceModel, batch["id"])
        assert row.status == "staff_attested_unverified"


def test_disposal_idempotence_stale_and_validation(db):
    service, sessions = db
    batch = record(service)
    a = request_disposal(service, batch["id"])
    b = request_disposal(service, batch["id"])
    assert b["id"] == a["id"]
    assert b["idempotent_replay"] is True
    with pytest.raises(CatinatBatchBlocked, match="invalid_disposal_proposed_quantity"):
        request_disposal(service, batch["id"], quantity="-1")
    with pytest.raises(CatinatBatchBlocked, match="invalid_disposal_proposed_quantity"):
        request_disposal(service, batch["id"], quantity="0")
    with pytest.raises(CatinatBatchBlocked, match="google_sheet_changed_reconfirm_required"):
        request_disposal(service, batch["id"],
                         current_readiness=lambda: source("b" * 64))
    with sessions() as session:
        assert session.query(InventoryCatinatApprovalRequestModel).count() == 1


def test_generic_approval_api_cannot_forge_waste_disposal(db):
    _, sessions = db
    with pytest.raises(Exception, match="waste_disposal_requires_verified_batch_source"):
        CatinatApprovalService(sessions).request(
            tenant_id="tenant-a", actor_id="staff-a",
            idempotency_key="generic-waste-key-123",
            business_date=DAY, kind="waste_disposal", subject_ref="fake-batch",
            proposal={"disposal_physically_verified": True},
            reason="Không có biên bản lô đúng nguồn",
        )


def _client(permissions, roles=None):
    principal = CurrentPrincipal(
        "actor", "tenant-a", "member", None,
        frozenset(roles or {"catinat_staff"}), frozenset(permissions),
        False, "session", "test",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/inventory")
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: principal
    return TestClient(app)


def test_route_requires_staff_and_review_permission_and_list_read(db):
    url = "/api/inventory/daily-sheet/catinat/batch-evidence"
    payload = {
        "idempotency_key": "batch-key-1234", "business_date": DAY.isoformat(),
        "expected_source_hash": "a" * 64, "sheet": "KHO PHA CHẾ", "cell": "L5",
        "lot_reference": "LOT-1920", "event_kind": "prepared",
        "event_at": EVENT.isoformat(), "note": "Có chứng cứ giờ từ nhân viên",
    }
    assert _client(set()).post(url, json=payload).status_code == 403
    assert _client({"inventory.review"}, {"viewer"}).post(url, json=payload).status_code == 403
    # Don't depend on production Sheets or DB from a permissions test.
    with patch("app.modules.inventory.daily.catinat_batch_router._service") as svc:
        svc.return_value.list.return_value = {"items": [], "sheets_writes": 0}
        with patch("app.modules.inventory.daily.catinat_batch_router._current",
                   return_value=source()):
            result = _client({"inventory.read"}).get(url + "?business_date=2026-10-20")
    assert result.status_code == 200, result.text
    assert result.json()["sheets_writes"] == 0
