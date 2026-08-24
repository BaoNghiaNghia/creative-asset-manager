from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel  # noqa: F401
from app.modules.inventory.daily_sheet.parser import (
    DailyCountRecord,
    InventoryQuantity,
    build_daily_count_variances,
    parse_inventory_quantity,
)
from app.modules.inventory.materials import MaterialRegistry, MaterialResolution
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.persistence_model import (
    InventoryItemAliasModel,
    InventoryItemModel,
    InventoryMaterialPackageConversionModel,
)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite://")
    event.listen(
        engine, "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    names = {
        "tenants", "inventory_items", "inventory_item_aliases",
        "inventory_material_external_identities",
        "inventory_material_package_conversions",
        "inventory_material_candidates",
    }
    for table in Base.metadata.sorted_tables:
        if table.name in names:
            table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"),
            TenantModel(id="tenant-b", name="Tenant B", slug="tenant-b"),
        ])
        session.commit()
    yield factory
    engine.dispose()


def item(session, *, tenant="tenant-a", name="Kem cheese", sku="CHEESE"):
    row = InventoryItemModel(
        tenant_id=tenant, sku=sku, name=name, base_unit="g",
        preferred_unit="g", canonical_dimension="mass", active=True,
    )
    session.add(row)
    session.flush()
    return row


def resolve(registry, *, name, key="27", tenant="tenant-a", matcher=None):
    return registry.resolve(
        tenant, source_id="sheet-1", external_key=key, raw_name=name,
        category="Topping", source_row=7, sheet="Daily",
        semantic_matcher=matcher,
        context={"source_cells": ["H7"], "nearby_rows": ["26", "28"]},
    )


def test_approved_alias_resolves_without_gemini_and_new_key_is_observed(sessions):
    with sessions() as session:
        material = item(session)
        session.add(InventoryItemAliasModel(
            tenant_id="tenant-a", item_id=material.id,
            alias="Cream cheese", normalized_alias="cream cheese",
        ))
        called = False

        def matcher(_context):
            nonlocal called
            called = True
            return None

        registry = MaterialRegistry(session)
        result = resolve(registry, name="Cream Cheese", key="40", matcher=matcher)
        assert result.status == "matched"
        assert result.material_id == material.id
        assert called is False
        registry.observe_match(
            "tenant-a", source_id="sheet-1", external_key="40",
            raw_name="Cream Cheese", material_id=material.id,
        )
        session.commit()
        assert resolve(registry, name="Cream Cheese", key="40").material_id == material.id


def test_new_material_invokes_semantic_matcher_with_scoped_registry_context(sessions):
    with sessions() as session:
        material = item(session)
        session.add(InventoryMaterialPackageConversionModel(
            tenant_id="tenant-a", item_id=material.id,
            package_name="Bich", normalized_package="bich",
            canonical_value=Decimal("1000"), canonical_unit="g",
            approved_by="admin",
        ))
        session.flush()
        captured = {}

        def matcher(context):
            captured.update(context)
            return {
                "material_id": material.id,
                "suggested_canonical_name": material.name,
                "confidence": 0.98,
                "reasons": ["same ingredient"],
            }

        result = resolve(registry=MaterialRegistry(session), name="Kem pho mai", key="41", matcher=matcher)
        assert result.status == "possible_rename"
        assert result.material_id == material.id
        assert result.interpretation_source == "gemini"
        assert captured["raw_row"]["item_key"] == "41"
        assert captured["source_context"]["source_cells"] == ["H7"]
        assert {entry["material_id"] for entry in captured["candidates"]} == {material.id}
        candidate_context = captured["candidates"][0]
        assert candidate_context["canonical_dimension"] == "mass"
        assert candidate_context["preferred_unit"] == "g"
        assert candidate_context["approved_package_conversions"]["bich"] == {
            "canonical_value": "1000.00000000",
            "canonical_unit": "g",
        }


def test_new_candidate_approval_creates_material_and_future_match_is_deterministic(sessions):
    with sessions() as session:
        registry = MaterialRegistry(session)
        result = resolve(registry, name="Sua Oat Milk", key="40", matcher=lambda _context: {
            "material_id": None, "suggested_canonical_name": "Sua Oat Milk",
            "confidence": 0.98, "reasons": ["new ingredient"],
        })
        candidate = registry.queue_candidate(
            "tenant-a", source_id="sheet-1", external_key="40",
            raw_name="Sua Oat Milk", category="Milk", source_row=40,
            sheet="Daily", resolution=result,
        )
        session.flush()
        material = registry.approve(
            "tenant-a", candidate.id, actor_id="admin",
            preferred_unit="ml", canonical_dimension="volume",
        )
        session.commit()
        matched = resolve(registry, name="Sua Oat Milk", key="40", matcher=lambda _context: pytest.fail("Gemini must not be called"))
        assert matched.status == "matched"
        assert matched.material_id == material.id


def test_external_identity_name_change_is_possible_rename_not_new_material(sessions):
    with sessions() as session:
        registry = MaterialRegistry(session)
        material = item(session)
        registry.observe_match(
            "tenant-a", source_id="sheet-1", external_key="27",
            raw_name="Kem cheese", material_id=material.id,
        )
        session.commit()
        renamed = resolve(registry, name="Kem pho mai")
        assert renamed.status == "possible_rename"
        assert renamed.material_id == material.id
        assert renamed.requires_review is True


def test_tenant_registry_context_is_isolated(sessions):
    with sessions() as session:
        item(session, tenant="tenant-b", name="Tenant B Secret", sku="B")
        captured = {}
        result = resolve(
            MaterialRegistry(session), tenant="tenant-a", name="Unknown",
            matcher=lambda context: captured.update(context) or None,
        )
        assert result.status == "new_material"
        assert captured["candidates"] == []


def test_ambiguous_approved_name_requires_review(sessions):
    with sessions() as session:
        first = item(session, name="Milk", sku="MILK-1")
        item(session, name="Alias Owner", sku="MILK-2")
        session.add(InventoryItemAliasModel(
            tenant_id="tenant-a", item_id=first.id,
            alias="Dairy", normalized_alias="dairy",
        ))
        second = session.query(InventoryItemModel).filter_by(sku="MILK-2").one()
        second.name = "Dairy"
        session.commit()
        result = resolve(MaterialRegistry(session), name="Dairy")
        assert result.status == "ambiguous"
        assert result.requires_review is True


def test_unknown_package_conversion_blocks_and_approved_conversion_is_deterministic(sessions):
    with pytest.raises(ValueError, match="unknown_package_conversion"):
        parse_inventory_quantity("2 bich + 300g")
    parsed = parse_inventory_quantity(
        "2 bich + 300g", {"bich": (Decimal("1000"), "g")}
    )
    assert parsed.canonical_value == Decimal("2300")
    assert parsed.canonical_unit == "g"


def test_registry_returns_only_approved_package_conversion_for_material(sessions):
    with sessions() as session:
        material = item(session)
        session.add(InventoryMaterialPackageConversionModel(
            tenant_id="tenant-a", item_id=material.id,
            package_name="Bich", normalized_package="bich",
            canonical_value=Decimal("1000"), canonical_unit="g",
            approved_by="admin",
        ))
        session.commit()
        conversions = MaterialRegistry(session).approved_package_conversions("tenant-a", material.id)
        assert parse_inventory_quantity("2 bich + 300g", conversions).canonical_value == Decimal("2300")


def record(key, name, value):
    quantity = InventoryQuantity(str(value), Decimal(str(value)), "count", ())
    return DailyCountRecord("main", key, name, "Category", int(key), quantity)


def test_added_and_removed_materials_are_not_interpreted_as_zero():
    variances, warnings = build_daily_count_variances(
        {("main", "40"): record("40", "New", 5)},
        {("main", "27"): record("27", "Removed", 9)},
    )
    by_status = {entry["material_status"]: entry for entry in variances}
    assert by_status["material_added"]["previous_canonical_quantity"] is None
    assert by_status["material_added"]["variance"] is None
    assert by_status["material_removed_or_missing"]["current_canonical_quantity"] is None
    assert by_status["material_removed_or_missing"]["variance"] is None
    assert {warning["code"] for warning in warnings} == {
        "material_added", "material_removed_or_missing"
    }


def test_auto_register_high_confidence_creates_canonical_material(sessions):
    matcher = lambda _context: {
        "material_id": None,
        "suggested_canonical_name": "Oat Milk",
        "confidence": 0.99,
        "reasons": ["new material"],
    }
    service = InventoryDailySheetService(sessions, material_semantic_matcher=matcher)
    with sessions() as session:
        registry = MaterialRegistry(session)
        records, semantic, unresolved = service._resolve_material_records(
            registry, "tenant-a", "sheet-1", "snapshot-1",
            {("main", "40"): record("40", "Sua Oat Milk", 5)},
            persist=True, new_material_policy="auto_register_high_confidence", sheet="Registry Test",
        )
        session.commit()
        assert unresolved == []
        material = session.query(InventoryItemModel).filter_by(
            tenant_id="tenant-a", name="Oat Milk"
        ).one()
        assert next(iter(records.values())).item_key == material.id
        assert semantic[0]["material_id"] == material.id
        assert resolve(
            registry, name="Sua Oat Milk", key="40",
            matcher=lambda _context: pytest.fail("approved identity must avoid Gemini"),
        ).material_id == material.id


def test_ignore_policy_does_not_persist_candidate(sessions):
    service = InventoryDailySheetService(sessions)
    with sessions() as session:
        registry = MaterialRegistry(session)
        _records, _semantic, unresolved = service._resolve_material_records(
            registry, "tenant-a", "sheet-1", "snapshot-1",
            {("main", "40"): record("40", "Unknown", 5)},
            persist=True, new_material_policy="ignore", sheet="Registry Test",
        )
        session.commit()
        assert unresolved[0]["status"] == "new_material"
        assert registry.list_candidates("tenant-a") == []
