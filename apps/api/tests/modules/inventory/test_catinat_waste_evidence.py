"""Q46 physical waste witness + separate manager countersign without stock writes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory.daily.catinat_approvals import CatinatApprovalService
from app.modules.inventory.daily.catinat_batch_evidence import CatinatBatchBlocked
from app.modules.inventory.daily.catinat_waste_evidence import CatinatWasteEvidenceService
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatWasteWitnessModel, InventoryCatinatWasteCertificationModel,
)
from tests.modules.inventory.test_catinat_batch_evidence import (
    DAY, EVENT, db, source, record, request_disposal,
)


def approval_and_batch(db):
    batch_service, sessions = db
    batch = record(batch_service)
    request = request_disposal(batch_service, batch["id"])
    approved = CatinatApprovalService(sessions).decide(
        tenant_id="tenant-a", actor_id="manager-b", request_id=request["id"],
        decision="approved", reason="Kiểm tra số lượng trước khi xác nhận hủy lô",
    )
    assert approved["status"] == "approved"
    return batch, request, CatinatWasteEvidenceService(sessions), sessions


def witness(service, approval, **overrides):
    inputs = {
        "tenant_id": "tenant-a", "actor_id": "witness-c",
        "approval_request_id": approval["id"], "business_date": DAY,
        "expected_source_hash": "a" * 64,
        "idempotency_key": "physical-waste-000001",
        "quantity": "1.5", "observed_at": EVENT + timedelta(hours=2),
        "method": "Hủy thực tế và ghi biên bản", "evidence_reference": "document-drive-file-12345",
        "note": "Trực tiếp chứng kiến việc hủy và lưu biên bản",
        "current_readiness": source,
    }
    return service.witness(**dict(inputs, **overrides))


def countersign(service, witness_record, **overrides):
    inputs = {
        "tenant_id": "tenant-a", "actor_id": "manager-b",
        "witness_id": witness_record["id"], "business_date": DAY,
        "expected_source_hash": "a" * 64,
        "note": "Đã đối chiếu chứng từ hủy và biên bản người chứng kiến",
        "current_readiness": source,
    }
    return service.countersign(**dict(inputs, **overrides))


def test_witness_and_manager_signature_are_two_immutable_records(db):
    batch, approved, service, sessions = approval_and_batch(db)
    first = witness(service, approved)
    assert first["status"] == "staff_witnessed_unposted"
    assert first["physical_evidence_countersigned"] is False
    assert first["ledger_posted"] is False
    assert first["inventory_writes"] == first["sheets_writes"] == 0
    signed = countersign(service, first)
    assert signed["manager_certified_by"] == "manager-b"
    assert signed["physical_evidence_countersigned"] is True
    assert signed["ledger_posted"] is False
    assert signed["ready_to_sign"] is False
    assert signed["automatic_carry_forward_allowed"] is False
    listed = service.list(tenant_id="tenant-a", business_date=DAY,
                          current_readiness=source)
    assert listed["items"][0]["physical_evidence_countersigned"] is True
    assert listed["stock_writes"] == listed["sheets_writes"] == 0
    with sessions() as s:
        assert s.query(InventoryCatinatWasteWitnessModel).count() == 1
        assert s.query(InventoryCatinatWasteCertificationModel).count() == 1


def test_replay_is_idempotent_but_any_change_conflicts(db):
    _, approved, service, sessions = approval_and_batch(db)
    first = witness(service, approved)
    assert witness(service, approved)["idempotent_replay"] is True
    with pytest.raises(CatinatBatchBlocked, match="waste_approval_batch_or_quantity_mismatch"):
        witness(service, approved, quantity="1.6")
    with pytest.raises(CatinatBatchBlocked, match="waste_witness_conflicting_replay"):
        witness(service, approved, actor_id="different-witness")
    second = countersign(service, first)
    assert countersign(service, first)["idempotent_replay"] is True
    with pytest.raises(CatinatBatchBlocked, match="waste_countersign_conflicting_replay"):
        countersign(service, first, note="Another manager note to conflict")
    assert second["ledger_posted"] is False


@pytest.mark.parametrize("actor", ["staff-a", "manager-b"])
def test_witness_must_not_be_batch_recorder_requestor_or_manager(db, actor):
    _, approved, service, _ = approval_and_batch(db)
    with pytest.raises(CatinatBatchBlocked, match="independent_physical_witness_required"):
        witness(service, approved, actor_id=actor)


@pytest.mark.parametrize("changes,expected", [
    ({"quantity": "1.6"}, "waste_approval_batch_or_quantity_mismatch"),
    ({"expected_source_hash": "b" * 64}, "google_sheet_changed_reconfirm_required"),
    ({"current_readiness": lambda: source("b" * 64)}, "google_sheet_changed_reconfirm_required"),
    ({"observed_at": EVENT - timedelta(hours=2)}, "disposal_before_batch_event"),
    ({"observed_at": datetime.now(timezone.utc) + timedelta(hours=1)}, "waste_observation_in_future"),
])
def test_unverifiable_or_wrong_quantity_witness_rejected(db, changes, expected):
    _, approved, service, _ = approval_and_batch(db)
    with pytest.raises(CatinatBatchBlocked, match=expected):
        witness(service, approved, **changes)


def test_source_change_after_witness_blocks_manager_countersign(db):
    _, approved, service, sessions = approval_and_batch(db)
    w = witness(service, approved)
    with pytest.raises(CatinatBatchBlocked, match="google_sheet_changed_reconfirm_required"):
        countersign(service, w, current_readiness=lambda: source("b" * 64))
    assert service.list(tenant_id="tenant-a", business_date=DAY,
                        current_readiness=source)["items"][0]["manager_certified_by"] is None
    with sessions() as s:
        assert s.query(InventoryCatinatWasteCertificationModel).count() == 0


def test_another_tenant_and_wrong_manager_cannot_countersign(db):
    _, approved, service, _ = approval_and_batch(db)
    w = witness(service, approved)
    with pytest.raises(CatinatBatchBlocked, match="waste_witness_not_found"):
        countersign(service, w, tenant_id="tenant-b")
    with pytest.raises(CatinatBatchBlocked, match="approved_manager_countersign_required"):
        countersign(service, w, actor_id="witness-c")
    with pytest.raises(CatinatBatchBlocked, match="approved_manager_countersign_required"):
        countersign(service, w, actor_id="manager-unrelated")


def test_missing_approval_cannot_create_physical_witness(db):
    _, approved, service, _ = approval_and_batch(db)
    with pytest.raises(CatinatBatchBlocked, match="waste_approval_not_found"):
        witness(service, approved, approval_request_id="not-approved-id")


def _client(permissions, roles):
    principal = CurrentPrincipal(
        "tester", "tenant-a", "member", None,
        frozenset(roles), frozenset(permissions),
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


def test_auth_witness_staff_only_and_countersign_manager_only(db):
    path = "/api/inventory/daily-sheet/catinat/waste-evidence"
    payload = {
        "approval_request_id": "approved-req", "business_date": DAY.isoformat(),
        "expected_source_hash": "a" * 64,
        "idempotency_key": "physical-waste-000001",
        "quantity": "1.5", "observed_at": EVENT.isoformat(),
        "method": "Hủy thực tế và ghi biên bản", "evidence_reference": "document-drive-file-12345",
        "note": "Xác minh đã hủy và đính kèm chứng cứ",
    }
    assert _client(set(), {"catinat_staff"}).post(path + "/witness", json=payload).status_code == 403
    assert _client({"inventory.review"}, {"viewer"}).post(path + "/witness", json=payload).status_code == 403
    assert _client({"inventory.review"}, {"catinat_staff"}).post(path + "/id/countersign", json={
        "business_date": DAY.isoformat(), "expected_source_hash": "a"*64,
        "note": "Quản lý xác minh biên bản đã ký",
    }).status_code == 403
    with patch("app.modules.inventory.daily.catinat_waste_router._service") as service:
        service.return_value.list.return_value = {"items": [], "sheets_writes": 0}
        response = _client({"inventory.read"}, {"viewer"}).get(
            path + "?business_date=2026-10-20"
        )
        assert response.status_code == 200
        assert response.json()["sheets_writes"] == 0
