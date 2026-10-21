"""Manager-certified exact-match evidence remains immutable and never activates stock writes."""
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
from app.modules.inventory.daily.catinat_approvals import CatinatApprovalService
from app.modules.inventory.daily.catinat_catalog_mapping import CatinatCatalogMappingPreview
from app.modules.inventory.daily.catinat_catalog_certifications import (
    CatinatCatalogCertificationService, CatinatCertificationBlocked,
)
from app.modules.inventory.daily_sheet.router import router
from app.modules.inventory.persistence_model import (
    InventoryCatinatCatalogCertificationModel,
    InventoryCatinatApprovalRequestModel,
    InventoryCatinatApprovalEventModel,
    InventoryItemModel, InventoryLocationModel,
)

DAY = date(2026, 10, 20)
REASON = "Need manager to confirm this exact item and warehouse mapping"
NOTE = "Verified this stock unit, warehouse and source evidence"


@pytest.fixture
def backend():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        TenantModel.__table__, InventoryItemModel.__table__,
        InventoryLocationModel.__table__,
        InventoryCatinatApprovalRequestModel.__table__,
        InventoryCatinatApprovalEventModel.__table__,
        InventoryCatinatCatalogCertificationModel.__table__,
    ])
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as s:
        s.add_all([
            TenantModel(id="tenant-a", name="Catinat", slug="catinat"),
            TenantModel(id="tenant-b", name="Other", slug="other"),
            InventoryItemModel(
                id="milk-a", tenant_id="tenant-a", sku="milk-a",
                name="SỮA ĐẶC", base_unit="gr", whole_unit="lon",
                fraction_unit="gr", conversion_factor=Decimal(380), active=True,
            ),
            InventoryLocationModel(
                id="bar-a", tenant_id="tenant-a", code="bar-a",
                name="KHO PHA CHẾ", active=True,
            ),
            InventoryItemModel(
                id="milk-b", tenant_id="tenant-b", sku="milk-b",
                name="SỮA ĐẶC", base_unit="gr", whole_unit="lon",
                conversion_factor=Decimal(380), active=True,
            ),
            InventoryLocationModel(
                id="bar-b", tenant_id="tenant-b", code="bar-b",
                name="KHO PHA CHẾ", active=True,
            ),
        ])
    yield CatinatApprovalService(sessions), CatinatCatalogCertificationService(sessions), sessions
    engine.dispose()


def source(sha="a" * 64):
    return {
        "business_date": DAY.isoformat(), "shift": "evening",
        "mode": "read_only_not_a_signed_close",
        "source_hash": sha, "spreadsheet_id": "trusted-workbook-a",
        "items": [{
            "sheet": "KHO PHA CHẾ", "cell": "L5", "material": "SỮA ĐẶC",
            "unit": "lon", "form": "whole", "raw": "3", "quantity": "3",
        }],
    }


def preview(sessions, sha="a" * 64):
    return CatinatCatalogMappingPreview(sessions).inspect(
        tenant_id="tenant-a", business_date=DAY, current_readiness=lambda: source(sha),
    )


def evidence(report):
    row = report["items"][0]
    return {
        "source_hash": report["source_hash"],
        "source_sheet": row["sheet"], "source_cell": row["cell"],
        "material_display": row["material"], "source_form": row["form"],
        "source_unit": row["unit"], "matching_status": row["status"],
        "candidate_item_ids": list(row["candidate_item_ids"]),
        "candidate_location_ids": list(report["locations"][0]["candidate_location_ids"]),
        "mapping_certified": False, "inventory_write_performed": False, "sheets_writes": 0,
    }


def create_approval(backend, *, sha="a" * 64, approved=True, idempotency="mapping-proof-20261020", proposal=None):
    approvals, _, sessions = backend
    proposal = evidence(preview(sessions, sha)) if proposal is None else proposal
    request = approvals.request(
        tenant_id="tenant-a", actor_id="staff-user", business_date=DAY,
        idempotency_key=idempotency, kind="catalog_mapping",
        subject_ref="KHO PHA CHẾ|L5", proposal=proposal, reason=REASON,
    )
    if approved:
        approvals.decide(
            tenant_id="tenant-a", actor_id="boss-user",
            request_id=request["id"], decision="approved", reason=NOTE,
        )
    return request["id"]


def certify(backend, request_id, *, sha="a" * 64, **changes):
    _, service, sessions = backend
    kwargs = dict(
        tenant_id="tenant-a", actor_id="boss-user", request_id=request_id,
        item_id="milk-a", location_id="bar-a", note=NOTE,
        current_readiness=lambda day: source(sha),
        current_preview=lambda day: preview(sessions, sha),
    )
    kwargs.update(changes)
    return service.certify(**kwargs)


def test_certification_is_immutable_versioned_evidence_with_no_stock_write(backend):
    request_id = create_approval(backend)
    result = certify(backend, request_id)
    assert result["version"] == 1
    assert result["source_hash"] == "a" * 64
    assert result["status"] == "evidence_only"
    assert result["mapping_activated"] is False
    assert result["ready_to_sign"] is False
    assert result["automatic_carry_forward_allowed"] is False
    assert result["inventory_writes"] == result["sheets_writes"] == 0
    assert result["approved_by"] == result["certified_by"] == "boss-user"
    second = certify(backend, request_id)
    assert second["id"] == result["id"]
    assert second["idempotent_replay"] is True
    _, service, sessions = backend
    assert len(service.list(tenant_id="tenant-a", business_date=DAY)) == 1
    assert service.list(tenant_id="tenant-b", business_date=DAY) == []
    with sessions() as s:
        assert len(list(s.scalars(select(InventoryCatinatCatalogCertificationModel)))) == 1


def test_explicit_new_approval_creates_next_version_not_an_overwrite(backend):
    one = certify(backend, create_approval(backend))
    two = certify(backend, create_approval(
        backend, idempotency="mapping-proof-20261020-new"))
    assert one["version"] == 1
    assert two["version"] == 2
    assert one["id"] != two["id"]


@pytest.mark.parametrize("change,code", [
    ({"item_id": "milk-b"}, "mapping_candidate_not_certifiable"),
    ({"location_id": "bar-b"}, "mapping_candidate_not_certifiable"),
    ({"actor_id": "staff-user"}, "certifying_manager_mismatch"),
    ({"actor_id": "other-manager"}, "certifying_manager_mismatch"),
    ({"tenant_id": "tenant-b"}, "approval_not_found"),
])
def test_cross_tenant_or_unapproved_selection_is_rejected(backend, change, code):
    request_id = create_approval(backend)
    with pytest.raises(CatinatCertificationBlocked, match=code):
        certify(backend, request_id, **change)


def test_unapproved_and_tampered_proposal_rejected(backend):
    pending = create_approval(backend, approved=False)
    with pytest.raises(CatinatCertificationBlocked, match="approved_catalog_request_required"):
        certify(backend, pending)
    approved = create_approval(backend, idempotency="mapping-proof-tamper")
    _, _, sessions = backend
    with sessions.begin() as s:
        row = s.get(InventoryCatinatApprovalRequestModel, approved)
        row.payload_json = dict(row.payload_json, sheets_writes=1)
    with pytest.raises(CatinatCertificationBlocked, match="approval_evidence_tampered"):
        certify(backend, approved)


def test_stale_sheet_and_catalog_require_fresh_human_review(backend):
    request_id = create_approval(backend)
    with pytest.raises(CatinatCertificationBlocked, match="mapping_source_changed"):
        certify(backend, request_id, sha="b" * 64)
    _, _, sessions = backend
    with pytest.raises(CatinatCertificationBlocked, match="mapping_source_changed"):
        certify(backend, request_id, current_preview=lambda day: dict(
            preview(sessions), spreadsheet_id="unexpected-workbook",
        ))
    with sessions.begin() as s:
        s.get(InventoryLocationModel, "bar-a").active = False

    with pytest.raises(CatinatCertificationBlocked, match="mapping_candidate_not_certifiable"):
        certify(backend, request_id)


def test_conflicting_replay_is_not_silent(backend):
    request_id = create_approval(backend)
    certify(backend, request_id)
    with pytest.raises(CatinatCertificationBlocked, match="certification_conflicting_replay"):
        certify(backend, request_id, note="Different review note must not silently replace old note")


def principal(user, perms, roles, tenant="tenant-a"):
    return CurrentPrincipal(
        user, tenant, f"member-{user}", None,
        frozenset(roles), frozenset(perms), False, f"session-{user}", "test",
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


API = "/api/inventory/daily-sheet/catinat/catalog-certifications"


def test_http_manager_role_required_and_get_is_read_only(backend):
    request_id = create_approval(backend)
    _, service, sessions = backend
    body = dict(
        approval_request_id=request_id, item_id="milk-a",
        location_id="bar-a", note=NOTE,
    )
    reader = client_for(principal("reader", {"inventory.read"}, {"viewer"}))
    operator = client_for(principal("op", {"inventory.finalize", "inventory.read"}, {"operator"}))
    manager = client_for(principal("boss-user", {"inventory.finalize", "inventory.read"}, {"tenant_admin"}))
    employee = client_for(principal("staff-user", {"inventory.finalize", "inventory.read"}, {"tenant_admin"}))
    with patch("app.modules.inventory.daily.catinat_certification_router._service",
               return_value=service), patch(
        "app.modules.inventory.daily.catinat_certification_router.load_evening_readiness",
        side_effect=lambda tenant, day, sessions: source(),
    ), patch("app.modules.inventory.daily.catinat_certification_router.CatinatCatalogMappingPreview") as loader:
        loader.return_value.inspect.side_effect = lambda **kwargs: preview(sessions)
        assert reader.post(API, json=body).status_code == 403
        assert operator.post(API, json=body).status_code == 403
        assert employee.post(API, json=body).status_code == 403
        result = manager.post(API, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["mapping_activated"] is False
        assert reader.get(API + "?business_date=2026-10-20").json()["items"][0]["id"] == result.json()["id"]
        assert reader.post(API, json=body).status_code == 403
