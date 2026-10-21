from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.materials import MaterialRegistry
from app.modules.inventory.persistence_model import (
    InventoryItemAliasModel,
    InventoryItemModel,
    InventoryMaterialCandidateModel,
    InventoryMaterialExternalIdentityModel,
    InventoryMaterialPackageConversionModel,
)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite://")
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
        session.add(TenantModel(id="tenant-a", name="Catinat", slug="catinat"))
    yield factory
    engine.dispose()


def _item(session, name, unit="g"):
    row = InventoryItemModel(
        tenant_id="tenant-a", sku=name.upper().replace(" ", "-"), name=name,
        base_unit=unit, preferred_unit=unit, canonical_dimension="mass", active=True,
    )
    session.add(row)
    session.flush()
    return row


def _candidate(session, name, key, unit="g", *, with_evidence=True):
    evidence = {
        "status": "explicit_unit_found",
        "unit_evidence": [{
            "value": unit, "value_cell": f"C{key}", "header_cell": "C4",
            "evidence_hash": "a" * 64,
        }],
    } if with_evidence else {"status": "missing_unit", "unit_evidence": []}
    row = InventoryMaterialCandidateModel(
        tenant_id="tenant-a", source_id="source-1", sheet="KHO PHA CHẾ",
        source_row=int(key), external_key=key, raw_name=name, category="Milk",
        status="new_material", context_json={"review_evidence": evidence},
    )
    session.add(row)
    session.flush()
    return row


def _selection(item, rows):
    return {
        "candidate_id": rows[0].id, "item_id": item.id,
        "expected_count": len(rows),
        "expected_candidate_ids": [row.id for row in rows],
    }


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
