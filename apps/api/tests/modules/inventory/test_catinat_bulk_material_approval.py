from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.principal import (
    CurrentPrincipal, ExternalIdentitySummary, require_authenticated_principal,
)
from app.modules.inventory.daily.catinat_material_grouping import grouped_material_candidates
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.materials import MaterialRegistry, MaterialResolution, material_candidate_group_evidence_hash
from app.modules.inventory.persistence_model import (
    InventoryItemAliasModel, InventoryItemModel, InventoryMaterialCandidateModel,
    InventoryMaterialExternalIdentityModel, InventoryMaterialPackageConversionModel,
)
from app.modules.inventory.router import router


PREVIEW = "/api/inventory/daily-sheet/catinat/catalog/material-candidate-groups"
APPROVE = "/api/inventory/materials/candidates/bulk-approve"


@pytest.fixture()
def sessions():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    required = {
        "tenants", "inventory_items", "inventory_item_aliases",
        "inventory_material_external_identities", "inventory_material_package_conversions",
        "inventory_material_candidates",
    }
    for table in Base.metadata.sorted_tables:
        if table.name in required:
            table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        for tenant in ("tenant-a", "tenant-b"):
            session.add(TenantModel(id=tenant, name=tenant, slug=tenant))
    yield factory
    engine.dispose()


def _item(session, name="Milk", unit="g", tenant="tenant-a"):
    row = InventoryItemModel(
        tenant_id=tenant, sku=str(uuid4()), name=name,
        base_unit=unit, preferred_unit=unit, canonical_dimension="mass", active=True,
    )
    session.add(row)
    session.flush()
    return row


def _candidate(session, name="Milk", key="5", unit="g", tenant="tenant-a", with_evidence=True):
    evidence = {
        "status": "explicit_unit_found",
        "name_cell": f"B{key}",
        "unit_evidence": [{
            "value": unit, "value_cell": f"C{key}", "header_cell": "C4",
            "header": "ĐVT", "evidence_hash": canonical_hash([unit]),
        }],
    } if with_evidence else {"status": "missing_unit", "unit_evidence": []}
    row = InventoryMaterialCandidateModel(
        tenant_id=tenant, source_id="source-1", sheet="KHO PHA CHẾ",
        source_row=int(key), external_key=key, raw_name=name, category="Milk",
        status="new_material", context_json={"review_evidence": evidence},
    )
    session.add(row)
    session.flush()
    return row


def _selection(item, rows):
    return {
        "candidate_id": min(rows, key=lambda row: (row.source_row, row.id)).id,
        "item_id": item.id, "expected_count": len(rows),
        "expected_candidate_ids": sorted(row.id for row in rows),
        "expected_evidence_hash": material_candidate_group_evidence_hash(rows, item),
    }


def _seed(sessions):
    with sessions.begin() as session:
        item = _item(session)
        rows = [_candidate(session, key="5"), _candidate(session, key="6")]
        return item, rows, _selection(item, rows)


def _actor(role="tenant_admin", permissions=("inventory.read", "inventory.finalize"),
           user="manager-user", tenant="tenant-a", platform_admin=False):
    return CurrentPrincipal(
        user, tenant, f"m-{user}",
        ExternalIdentitySummary("google", "provider-subject-is-not-the-audit-actor", None),
        frozenset({role}), frozenset(permissions), platform_admin,
        f"s-{user}", "test",
    )


def _client(actor):
    app = FastAPI()
    app.include_router(router)
    if actor is not None:
        app.dependency_overrides[require_authenticated_principal] = lambda: actor
    return TestClient(app)


def test_ui_preview_and_bulk_route_contract_and_exact_retry(sessions):
    item, rows, spec = _seed(sessions)
    manager = _client(_actor())
    with patch("app.modules.inventory.router.SessionLocal", sessions), patch(
        "app.modules.inventory.daily_sheet.router.SessionLocal", sessions,
    ):
        preview = manager.get(PREVIEW)
        assert preview.status_code == 200, preview.text
        report = preview.json()
        assert report["can_approve_candidates"] is True
        assert report["source_rows"] == 2
        assert report["automatic_approvals"] == report["inventory_writes"] == report["sheet_writes"] == 0
        group = report["groups"][0]
        assert group["certified"] is False
        approved = group["approval_groups"][0]
        assert approved["can_bulk_approve"] is True
        assert approved["evidence_hash"] == spec["expected_evidence_hash"]
        assert approved["candidate_ids"] == spec["expected_candidate_ids"]
        assert len(group["source_rows"]) == 2
        first = manager.post(APPROVE, json={"groups": [spec]})
        assert first.status_code == 200, first.text
        assert first.json()["approved_groups"] == 1
        assert first.json()["approved_candidates"] == 2
        assert first.json()["idempotent_replay"] is False
        assert first.json()["inventory_balance_writes"] == first.json()["google_sheet_writes"] == 0
        repeated = manager.post(APPROVE, json={"groups": [spec]})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["idempotent_replay"] is True
        assert manager.get(PREVIEW).json()["groups"] == []
    with sessions() as session:
        actual = list(session.scalars(select(InventoryMaterialCandidateModel)))
        assert all(row.status == "approved" for row in actual)
        assert all(row.context_json["approved_by"] == "manager-user" for row in actual)
        assert len(list(session.scalars(select(InventoryItemAliasModel)))) == 1
        assert len(list(session.scalars(select(InventoryMaterialExternalIdentityModel)))) == 2
        assert list(session.scalars(select(InventoryMaterialPackageConversionModel))) == []


@pytest.mark.parametrize("actor", [
    _actor(role="operator", permissions=("inventory.read", "inventory.review")),
    _actor(role="operator"),
    _actor(role="tenant_admin", permissions=("inventory.read",)),
])
def test_staff_and_incomplete_manager_roles_can_preview_but_not_approve(sessions, actor):
    _, _, spec = _seed(sessions)
    with patch("app.modules.inventory.router.SessionLocal", sessions), patch(
        "app.modules.inventory.daily_sheet.router.SessionLocal", sessions,
    ):
        client = _client(actor)
        report = client.get(PREVIEW)
        assert report.status_code == 200
        assert report.json()["can_approve_candidates"] is False
        assert client.post(APPROVE, json={"groups": [spec]}).status_code == 403
    with sessions() as session:
        assert all(row.status == "new_material" for row in session.scalars(select(InventoryMaterialCandidateModel)))


def test_platform_admin_capability_uses_same_authoritative_guard(sessions):
    _seed(sessions)
    with patch("app.modules.inventory.daily_sheet.router.SessionLocal", sessions):
        response = _client(_actor(role="viewer", permissions=(), platform_admin=True)).get(PREVIEW)
    assert response.status_code == 200
    assert response.json()["can_approve_candidates"] is True


def test_cross_tenant_and_missing_permission_cannot_read_or_approve(sessions):
    _, _, spec = _seed(sessions)
    with patch("app.modules.inventory.router.SessionLocal", sessions), patch(
        "app.modules.inventory.daily_sheet.router.SessionLocal", sessions,
    ):
        foreign = _client(_actor(tenant="tenant-b"))
        assert foreign.get(PREVIEW).json()["groups"] == []
        assert foreign.post(APPROVE, json={"groups": [spec]}).status_code == 404
        assert _client(_actor(permissions=())).get(PREVIEW).status_code == 403


@pytest.mark.parametrize("field", ["unit_evidence", "source_id", "catalog_unit"])
def test_changed_source_or_catalog_evidence_blocks_previously_reviewed_ids(sessions, field):
    item, rows, spec = _seed(sessions)
    with sessions.begin() as session:
        row = session.get(InventoryMaterialCandidateModel, rows[0].id)
        catalog = session.get(InventoryItemModel, item.id)
        if field == "unit_evidence":
            evidence = deepcopy(row.context_json)
            evidence["review_evidence"]["unit_evidence"][0]["value_cell"] = "D5"
            row.context_json = evidence
        elif field == "source_id":
            row.source_id = "different-snapshot"
        else:
            catalog.whole_unit = "box"
    with sessions.begin() as session:
        with pytest.raises(ValueError, match="candidate_group_evidence_changed"):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=[spec], actor_id="manager-user",
            )
    with sessions() as session:
        assert all(row.status == "new_material" for row in session.scalars(select(InventoryMaterialCandidateModel)))


def test_added_candidate_cannot_be_silently_included(sessions):
    _, _, spec = _seed(sessions)
    with sessions.begin() as session:
        _candidate(session, key="7")
    with sessions.begin() as session:
        with pytest.raises(ValueError, match="candidate_group_changed"):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=[spec], actor_id="manager-user",
            )


@pytest.mark.parametrize("mode", ["missing", "wrong", "malformed", "duplicate_catalog"])
def test_uncertified_units_and_ambiguous_catalog_fail_before_any_group_mutation(sessions, mode):
    with sessions.begin() as session:
        milk = _item(session)
        valid = _candidate(session)
        syrup = _item(session, name="Syrup")
        invalid = _candidate(session, name="Syrup", key="6",
                             unit="bottle" if mode == "wrong" else "g",
                             with_evidence=mode != "missing")
        if mode == "malformed":
            invalid.context_json = {"review_evidence": {
                "status": "explicit_unit_found", "unit_evidence": [None],
            }}
        if mode == "duplicate_catalog":
            _item(session, name="Syrup")
        specs = [_selection(milk, [valid]), _selection(syrup, [invalid])]
        report = grouped_material_candidates(session, "tenant-a")
        group = next(group for group in report["groups"] if group["name"] == "Syrup")
        assert group["approval_groups"][0]["can_bulk_approve"] is False
        with pytest.raises(ValueError):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=specs, actor_id="manager-user",
            )
        assert valid.status == invalid.status == "new_material"


def test_exact_retry_cannot_change_actor_selection_or_reviewed_evidence(sessions):
    _, _, spec = _seed(sessions)
    with sessions.begin() as session:
        MaterialRegistry(session).approve_equivalent_batch(
            "tenant-a", groups=[spec], actor_id="manager-user",
        )
    for change in ("actor", "subset", "hash"):
        altered = deepcopy(spec)
        actor = "manager-user"
        if change == "actor":
            actor = "other-manager"
        elif change == "subset":
            altered["expected_candidate_ids"] = [altered["candidate_id"]]
            altered["expected_count"] = 1
        else:
            altered["expected_evidence_hash"] = "f" * 64
        with sessions.begin() as session:
            with pytest.raises(ValueError, match="candidate_batch_idempotency_conflict"):
                MaterialRegistry(session).approve_equivalent_batch(
                    "tenant-a", groups=[altered], actor_id=actor,
                )


def test_request_without_preview_hash_is_rejected_by_api(sessions):
    _, _, spec = _seed(sessions)
    spec.pop("expected_evidence_hash")
    with patch("app.modules.inventory.router.SessionLocal", sessions):
        response = _client(_actor()).post(APPROVE, json={"groups": [spec]})
    assert response.status_code == 422


def test_duplicate_selection_is_rejected_and_no_rows_change(sessions):
    _, _, spec = _seed(sessions)
    with sessions.begin() as session:
        with pytest.raises(ValueError, match="candidate_batch_duplicate_selection"):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=[spec, spec], actor_id="manager-user",
            )
    with sessions() as session:
        assert all(row.status == "new_material" for row in session.scalars(select(InventoryMaterialCandidateModel)))

@pytest.mark.parametrize("action", ["approve", "reject", "ignore"])
def test_legacy_candidate_mutations_reject_completed_bulk_state_and_preserve_replay(sessions, action):
    item, rows, spec = _seed(sessions)
    manager = _client(_actor())
    reviewer = _client(_actor(
        role="operator", permissions=("inventory.read", "inventory.review"), user="stale-reviewer",
    ))
    with patch("app.modules.inventory.router.SessionLocal", sessions):
        approved = manager.post(APPROVE, json={"groups": [spec]})
        assert approved.status_code == 200, approved.text
        response = reviewer.post(
            f"/api/inventory/materials/candidates/{rows[0].id}/{action}",
            json={"item_id": item.id} if action == "approve" else {},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "material_candidate_not_pending"
        replayed = manager.post(APPROVE, json={"groups": [spec]})
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["idempotent_replay"] is True
    with sessions() as session:
        actual = list(session.scalars(select(InventoryMaterialCandidateModel)))
        assert all(row.status == "approved" for row in actual)
        assert all(row.context_json["approved_by"] == "manager-user" for row in actual)
        assert len(list(session.scalars(select(InventoryItemAliasModel)))) == 1
        assert len(list(session.scalars(select(InventoryMaterialExternalIdentityModel)))) == 2


@pytest.mark.parametrize("action", ["approve", "reject", "ignore"])
def test_legacy_candidate_mutations_do_not_access_a_foreign_candidate(sessions, action):
    item, rows, _ = _seed(sessions)
    reviewer = _client(_actor(
        role="operator", permissions=("inventory.review",), user="other-reviewer", tenant="tenant-b",
    ))
    with patch("app.modules.inventory.router.SessionLocal", sessions):
        response = reviewer.post(
            f"/api/inventory/materials/candidates/{rows[0].id}/{action}",
            json={"item_id": item.id} if action == "approve" else {},
        )
        assert response.status_code == 404, response.text
    with sessions() as session:
        assert all(row.status == "new_material" for row in session.scalars(select(InventoryMaterialCandidateModel)))
        assert list(session.scalars(select(InventoryItemAliasModel))) == []
        assert list(session.scalars(select(InventoryMaterialExternalIdentityModel))) == []


@pytest.mark.parametrize("mutation", ["single_approve", "queue_context"])
def test_cached_candidate_mutations_preserve_completed_bulk_evidence(sessions, mutation):
    item, rows, spec = _seed(sessions)
    original_evidence = deepcopy(rows[0].context_json["review_evidence"])
    with sessions() as stale_session:
        cached = stale_session.get(InventoryMaterialCandidateModel, rows[0].id)
        assert cached.status == "new_material"
        with sessions.begin() as winner:
            MaterialRegistry(winner).approve_equivalent_batch(
                "tenant-a", groups=[spec], actor_id="manager-user",
            )
        assert cached.status == "new_material"
        registry = MaterialRegistry(stale_session)
        if mutation == "single_approve":
            with pytest.raises(ValueError, match="material_candidate_not_pending"):
                registry.approve(
                    "tenant-a", cached.id, actor_id="stale-reviewer", item_id=item.id,
                )
            stale_session.rollback()
        else:
            registry.queue_candidate(
                "tenant-a", source_id=cached.source_id, external_key=cached.external_key,
                raw_name=cached.raw_name, category=cached.category,
                source_row=cached.source_row, sheet=cached.sheet,
                resolution=MaterialResolution(
                    "new_material", None, cached.raw_name, cached.raw_name,
                    cached.category, Decimal("0"), ("no_approved_match",), True,
                ),
                context={"review_evidence": {"status": "missing_unit", "unit_evidence": []}},
            )
            stale_session.commit()
    with sessions.begin() as session:
        actual = session.get(InventoryMaterialCandidateModel, rows[0].id)
        assert actual.status == "approved"
        assert actual.context_json["approved_by"] == "manager-user"
        assert actual.context_json["review_evidence"] == original_evidence
        assert "bulk_approval" in actual.context_json
        replay = MaterialRegistry(session).approve_equivalent_batch(
            "tenant-a", groups=[spec], actor_id="manager-user",
        )
        assert replay["idempotent_replay"] is True


# Retain main's original error-code and all-or-none regressions, using the
# shared selection helper that now binds the reviewed evidence hash.
def test_bulk_approval_requires_explicit_hashed_unit_evidence_and_is_idempotently_scoped(sessions):
    with sessions() as session:
        material = _item(session, "Milk")
        rows = [_candidate(session, "Milk", "5"), _candidate(session, "Milk", "6")]
        result = MaterialRegistry(session).approve_equivalent_batch(
            "tenant-a", groups=[_selection(material, rows)], actor_id="manager-1",
        )
        assert result["approved_groups"] == 1
        assert result["approved_candidates"] == 2
        assert result["inventory_balance_writes"] == 0
        assert result["google_sheet_writes"] == 0
        assert [row.status for row in rows] == ["approved", "approved"]


def test_bulk_approval_preflights_every_group_before_any_mutation(sessions):
    with sessions() as session:
        milk = _item(session, "Milk")
        syrup = _item(session, "Syrup")
        valid = _candidate(session, "Milk", "5")
        invalid = _candidate(session, "Syrup", "6", unit="bottle")
        with pytest.raises(ValueError, match="candidate_unit_not_certified"):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a",
                groups=[_selection(milk, [valid]), _selection(syrup, [invalid])],
                actor_id="manager-1",
            )
        assert valid.status == "new_material"
        assert invalid.status == "new_material"


def test_bulk_approval_blocks_missing_unit_evidence(sessions):
    with sessions() as session:
        material = _item(session, "Milk")
        row = _candidate(session, "Milk", "5", with_evidence=False)
        with pytest.raises(ValueError, match="candidate_unit_evidence_missing"):
            MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=[_selection(material, [row])], actor_id="manager-1",
            )
        assert row.status == "new_material"
