from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.material_evidence import (
    MaterialCandidateEvidenceService,
    build_material_review_evidence,
)
from app.modules.inventory.persistence_model import (
    InventoryMaterialCandidateModel,
    InventorySettingsModel,
)


def cell(sheet: str, address: str, value):
    return {
        "sheet": sheet,
        "cell": address,
        "raw_value": value,
        "evidence_hash": canonical_hash([value]),
    }


def test_material_review_evidence_recognizes_vietnamese_unit_headers():
    for header in ("ĐVT", "Đơn vị", "Đơn vị tính"):
        result = build_material_review_evidence(
            raw_name="SỮA TƯƠI*",
            name_evidence={
                "sheet": "PHA CHẾ",
                "cell": "A5",
                "evidence_hash": canonical_hash(["SỮA TƯƠI*"]),
            },
            cells=[
                cell("PHA CHẾ", "A4", "Nguyên liệu"),
                cell("PHA CHẾ", "B4", header),
                cell("PHA CHẾ", "A5", "SỮA TƯƠI*"),
                cell("PHA CHẾ", "B5", "L"),
            ],
        )
        assert result["status"] == "explicit_unit_found"
        assert result["suggested_preferred_unit"] == "L"
        assert result["suggested_canonical_dimension"] == "volume"


def test_material_review_evidence_detects_explicit_volume_unit():
    cells = [
        cell("PHA CHẾ", "A4", "Nguyên liệu"),
        cell("PHA CHẾ", "B4", "ĐVT"),
        cell("PHA CHẾ", "A5", "SỮA TƯƠI*"),
        cell("PHA CHẾ", "B5", "L"),
    ]

    result = build_material_review_evidence(
        raw_name="SỮA TƯƠI*",
        name_evidence={
            "sheet": "PHA CHẾ",
            "cell": "A5",
            "evidence_hash": canonical_hash(["SỮA TƯƠI*"]),
        },
        cells=cells,
    )

    assert result["status"] == "explicit_unit_found"
    assert result["suggested_preferred_unit"] == "L"
    assert result["suggested_canonical_dimension"] == "volume"
    assert result["unit_evidence"] == [
        {
            "header_cell": "B4",
            "header": "ĐVT",
            "value_cell": "B5",
            "value": "L",
            "evidence_hash": canonical_hash(["L"]),
        }
    ]


def test_material_review_evidence_keeps_unknown_unit_for_human_review():
    cells = [
        cell("KHO", "C3", "Đơn vị tính"),
        cell("KHO", "A8", "COMBO CHIÊN"),
        cell("KHO", "C8", "khay"),
    ]

    result = build_material_review_evidence(
        raw_name="COMBO CHIÊN",
        name_evidence={
            "sheet": "KHO",
            "cell": "A8",
            "evidence_hash": canonical_hash(["COMBO CHIÊN"]),
        },
        cells=cells,
    )

    assert result["status"] == "explicit_unit_found"
    assert result["suggested_preferred_unit"] == "khay"
    assert "suggested_canonical_dimension" not in result


def test_material_review_evidence_rejects_stale_name_evidence():
    result = build_material_review_evidence(
        raw_name="BỘT CACAO",
        name_evidence={
            "sheet": "PHA CHẾ",
            "cell": "A10",
            "evidence_hash": canonical_hash(["BỘT CACAO"]),
        },
        cells=[cell("PHA CHẾ", "A10", "BỘT MATCHA")],
    )

    assert result["status"] == "name_evidence_mismatch"
    assert result["unit_evidence"] == []


def test_material_candidate_evidence_service_persists_review_evidence():
    engine = create_engine("sqlite://")
    for table in Base.metadata.sorted_tables:
        if table.name in {
            "tenants",
            "external_sources",
            "inventory_settings",
            "inventory_material_candidates",
        }:
            table.create(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        source = ExternalSourceModel(
            id="source-1",
            tenant_id="tenant-a",
            source_key="inventory-source",
            source_type="google_drive",
            source_metadata={"oauth_connection_id": "connection-1"},
            status="active",
        )
        session.add(source)
        session.add(
            InventorySettingsModel(
                tenant_id="tenant-a",
                enabled=True,
                daily_sheet_automation_enabled=True,
                external_source_id=source.id,
                inbox_folder_id="inbox",
            )
        )
        session.add(
            InventoryMaterialCandidateModel(
                tenant_id="tenant-a",
                source_id="sheet-1",
                sheet="PHA CHẾ",
                source_row=5,
                external_key="carry-forward:PHA CHẾ:A5",
                raw_name="SỮA TƯƠI*",
                category="Unknown",
                status="new_material",
                confidence=0,
                reasons_json=["carry_forward_missing_catalog_item"],
                context_json={
                    "origin": "carry_forward_missing_catalog_item",
                    "name_evidence": {
                        "sheet": "PHA CHẾ",
                        "cell": "A5",
                        "evidence_hash": canonical_hash(["SỮA TƯƠI*"]),
                    },
                },
            )
        )
        session.commit()

    class Google:
        def __init__(self, token):
            assert token == "token"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def batch_get_values(self, spreadsheet_id, ranges, **_kwargs):
            assert spreadsheet_id == "sheet-1"
            assert len(ranges) == 2
            return [
                {"range": ranges[0], "values": [["Nguyên liệu", "ĐVT"]]},
                {"range": ranges[1], "values": [["SỮA TƯƠI*", "L"]]},
            ]

    service = MaterialCandidateEvidenceService(
        sessions,
        client_factory=Google,
        token_resolver=lambda _connection_id: "token",
    )
    result = service.refresh_pending("tenant-a")

    assert result == {"updated": 1, "statuses": {"explicit_unit_found": 1}}
    with sessions() as session:
        candidate = session.scalar(select(InventoryMaterialCandidateModel))
        evidence = candidate.context_json["review_evidence"]
        assert evidence["suggested_preferred_unit"] == "L"
        assert evidence["suggested_canonical_dimension"] == "volume"
        assert evidence["unit_evidence"][0]["header"] == "ĐVT"

    engine.dispose()
