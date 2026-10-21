"""Controlled committed-session races; SQLite does not prove PostgreSQL lock scheduling."""
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.material_evidence import (
    MaterialCandidateEvidenceService, MaterialEvidenceError,
)
from app.modules.inventory.materials import (
    MaterialRegistry, material_candidate_group_evidence_hash,
)
from app.modules.inventory.persistence_model import (
    InventoryItemModel, InventoryMaterialCandidateModel, InventorySettingsModel,
)


@pytest.fixture()
def candidate_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'material-refresh.db'}")
    required = {
        "tenants", "external_sources", "inventory_settings", "inventory_items",
        "inventory_item_aliases", "inventory_material_external_identities",
        "inventory_material_package_conversions", "inventory_material_candidates",
    }
    for table in Base.metadata.sorted_tables:
        if table.name in required:
            table.create(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        for tenant in ("tenant-a", "tenant-b"):
            session.add(TenantModel(id=tenant, name=tenant, slug=tenant))
        session.add(ExternalSourceModel(
            id="configured-source", tenant_id="tenant-a",
            source_key="inventory-source", source_type="google_drive",
            source_metadata={"oauth_connection_id": "connection-1"}, status="active",
        ))
        session.add(InventorySettingsModel(
            tenant_id="tenant-a", enabled=True, daily_sheet_automation_enabled=True,
            external_source_id="configured-source", inbox_folder_id="inbox",
        ))
        item = InventoryItemModel(
            tenant_id="tenant-a", sku="milk", name="SỮA TƯƠI*",
            base_unit="L", preferred_unit="L", canonical_dimension="volume", active=True,
        )
        session.add(item)
        rows = []
        for number in (5, 6):
            name_hash = canonical_hash(["SỮA TƯƠI*"])
            row = InventoryMaterialCandidateModel(
                tenant_id="tenant-a", source_id="sheet-1", sheet="PHA CHẾ",
                source_row=number, external_key=f"carry-forward:PHA CHẾ:A{number}",
                raw_name="SỮA TƯƠI*", category="Milk", status="new_material",
                context_json={
                    "origin": "carry_forward_missing_catalog_item",
                    "name_evidence": {
                        "sheet": "PHA CHẾ", "cell": f"A{number}",
                        "evidence_hash": name_hash,
                    },
                    "review_evidence": {
                        "status": "explicit_unit_found", "name_cell": f"A{number}",
                        "name_evidence_hash": name_hash,
                        "unit_evidence": [{
                            "header": "ĐVT", "header_cell": "B1",
                            "value": "L", "value_cell": f"B{number}",
                            "evidence_hash": canonical_hash(["L"]),
                        }],
                        "refreshed_at": "2026-10-20T00:00:00+00:00",
                    },
                },
            )
            session.add(row)
            rows.append(row)
        session.flush()
        ids = [row.id for row in rows]
        spec = {
            "candidate_id": rows[0].id, "item_id": item.id, "expected_count": len(rows),
            "expected_candidate_ids": sorted(ids),
            "expected_evidence_hash": material_candidate_group_evidence_hash(rows, item),
        }
        original = {row.id: deepcopy(row.context_json) for row in rows}
    yield engine, sessions, ids, spec, original
    engine.dispose()


class ReadOnlyGoogle:
    """Only read methods exist: a write attempt is a test failure."""

    def __init__(self, token):
        assert token == "test-token"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def batch_get_values(self, spreadsheet_id, ranges, **_kwargs):
        assert spreadsheet_id == "sheet-1"
        return [
            {"range": ranges[0], "values": [["Nguyên liệu", "ĐVT"]]},
            *[
                {"range": region, "values": [["SỮA TƯƠI*", "L"]]}
                for region in ranges[1:]
            ],
        ]


def _interleaved_factory(engine, sessions, ids, mutation):
    """Commit a competitor after source read, with stale ORM instances cached.

    A real locking database serializes the final read against approvals. This
    fixture verifies stale identity-map state is refreshed and revalidated; it
    deliberately makes no claim about SQLite FOR UPDATE behavior.
    """
    class FinalReadSession(Session):
        def scalars(self, statement, *args, **kwargs):
            if not self.info.get("interleaved") and any(
                entry.get("entity") is InventoryMaterialCandidateModel
                for entry in getattr(statement, "column_descriptions", ())
            ):
                self.info["interleaved"] = True
                # Keep strong references so the old unrefreshed query can
                # reproduce the lost-context bug.
                self.info["cached_candidates"] = [
                    self.get(InventoryMaterialCandidateModel, row_id) for row_id in ids
                ]
                mutation()
            return super().scalars(statement, *args, **kwargs)

    final_sessions = sessionmaker(engine, class_=FinalReadSession, expire_on_commit=False)
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return sessions() if calls == 1 else final_sessions()

    return factory


def _service(factory):
    return MaterialCandidateEvidenceService(
        factory, client_factory=ReadOnlyGoogle,
        token_resolver=lambda _connection_id: "test-token",
    )


def test_refresh_preserves_competing_bulk_approval_and_exact_retry(candidate_store):
    engine, sessions, ids, spec, _original = candidate_store
    committed = {}

    def approve():
        with sessions.begin() as session:
            outcome = MaterialRegistry(session).approve_equivalent_batch(
                "tenant-a", groups=[spec], actor_id="manager-user",
            )
            assert outcome["idempotent_replay"] is False
            for row in session.scalars(select(InventoryMaterialCandidateModel)):
                committed[row.id] = deepcopy(row.context_json)

    service = _service(_interleaved_factory(engine, sessions, ids, approve))
    with pytest.raises(MaterialEvidenceError, match="material_evidence_candidate_changed"):
        service.refresh_pending("tenant-a")
    with sessions.begin() as session:
        rows = list(session.scalars(select(InventoryMaterialCandidateModel)))
        assert all(row.status == "approved" for row in rows)
        assert all(row.context_json == committed[row.id] for row in rows)
        assert all(row.context_json["approved_by"] == "manager-user" for row in rows)
        assert all(row.context_json["bulk_approval"]["batch_hash"] for row in rows)
        replay = MaterialRegistry(session).approve_equivalent_batch(
            "tenant-a", groups=[spec], actor_id="manager-user",
        )
        assert replay["idempotent_replay"] is True


@pytest.mark.parametrize("field", [
    "tenant_id", "source_id", "sheet", "source_row", "external_key", "raw_name",
    "category", "status", "name_evidence", "review_evidence", "origin",
])
def test_refresh_revalidates_source_and_context_without_partial_write(candidate_store, field):
    engine, sessions, ids, _spec, original = candidate_store
    changed = {}

    def mutate():
        with sessions.begin() as session:
            row = session.get(InventoryMaterialCandidateModel, ids[0])
            if field == "name_evidence":
                context = deepcopy(row.context_json)
                context["name_evidence"]["cell"] = "C5"
                row.context_json = context
            elif field == "review_evidence":
                context = deepcopy(row.context_json)
                context["review_evidence"]["refreshed_at"] = "2026-10-21T00:00:00+00:00"
                row.context_json = context
            elif field == "origin":
                row.context_json = {**row.context_json, "origin": "different-observation"}
            else:
                replacements = {
                    "tenant_id": "tenant-b", "source_id": "different-workbook",
                    "sheet": "KHO", "source_row": 9, "external_key": "different-source-key",
                    "raw_name": "BỘT CACAO", "category": "Cocoa", "status": "possible_rename",
                }
                setattr(row, field, replacements[field])
            changed["context"] = deepcopy(row.context_json)
            changed["value"] = getattr(row, field, None)

    service = _service(_interleaved_factory(engine, sessions, ids, mutate))
    with pytest.raises(MaterialEvidenceError, match="material_evidence_candidate_changed"):
        service.refresh_pending("tenant-a")
    with sessions() as session:
        row = session.get(InventoryMaterialCandidateModel, ids[0])
        untouched = session.get(InventoryMaterialCandidateModel, ids[1])
        assert row.context_json == changed["context"]
        if field not in {"name_evidence", "review_evidence", "origin"}:
            assert getattr(row, field) == changed["value"]
        assert untouched.context_json == original[ids[1]]
        assert untouched.status == "new_material"
