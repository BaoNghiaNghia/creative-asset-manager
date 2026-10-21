"""Read-only Catinat coverage of current manager certification evidence.

A matching archived certification is *evidence* only, not an active mapping,
a closing authorization, stock ledger posting or Google Sheets write.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import _key, CatinatCountBlocked
from app.modules.inventory.persistence_model import InventoryCatinatCatalogCertificationModel

SHEETS = ("KHO PHA CHẾ", "PHÒNG PHA CHẾ", "KHO PHÒNG RANG")


def catalog_evidence_coverage(
    sessions: sessionmaker[Session], *, tenant_id: str, business_date: date,
    report: dict,
) -> dict:
    if (report.get("business_date") != business_date.isoformat()
            or report.get("mode") != "read_only_uncertified_catalog_candidates"
            or not isinstance(report.get("items"), list)
            or not isinstance(report.get("locations"), list)
            or not isinstance(report.get("source_hash"), str)
            or len(report["source_hash"]) != 64
            or not isinstance(report.get("spreadsheet_id"), str)
            or not report["spreadsheet_id"]):
        raise CatinatCountBlocked("catalog_preview_untrusted")
    rows = report["items"]
    identities = [_key(row) for row in rows]
    if len(identities) != len(set(identities)):
        raise CatinatCountBlocked("duplicate_source_count_identity")
    areas = {sheet: {"sheet": sheet, "source_cells": 0, "evidence_cells": 0,
                     "pending_cells": 0, "missing_location": True,
                     "ambiguous_location": False} for sheet in SHEETS}
    places = report["locations"]
    if len(places) != len(SHEETS) or {p.get("sheet") for p in places} != set(SHEETS):
        raise CatinatCountBlocked("catalog_location_scope_untrusted")
    by_sheet = {p["sheet"]: p for p in places}
    for sheet in SHEETS:
        status = by_sheet[sheet].get("status")
        ids = by_sheet[sheet].get("candidate_location_ids")
        if (status not in {"no_exact_location", "ambiguous_location", "exact_name_candidate"}
                or not isinstance(ids, list)):
            raise CatinatCountBlocked("catalog_location_scope_untrusted")
        areas[sheet]["missing_location"] = status == "no_exact_location"
        areas[sheet]["ambiguous_location"] = status == "ambiguous_location"

    with sessions() as session:
        certificates = session.scalars(select(InventoryCatinatCatalogCertificationModel).where(
            InventoryCatinatCatalogCertificationModel.tenant_id == tenant_id,
            InventoryCatinatCatalogCertificationModel.business_date == business_date,
            InventoryCatinatCatalogCertificationModel.spreadsheet_file_id == report["spreadsheet_id"],
            InventoryCatinatCatalogCertificationModel.source_hash == report["source_hash"],
            InventoryCatinatCatalogCertificationModel.status == "evidence_only",
        ).order_by(
            InventoryCatinatCatalogCertificationModel.version.desc(),
            InventoryCatinatCatalogCertificationModel.certified_at.desc(),
        )).all()
    # Only the latest approved evidence version for a cell counts. Historical
    # entries remain in the immutable ledger, not a second confirmation.
    latest = {}
    for evidence in certificates:
        latest.setdefault((evidence.sheet_name, evidence.cell_ref), evidence)
    items = []
    for row in rows:
        sheet, cell = _key(row)
        if sheet not in areas:
            raise CatinatCountBlocked("catalog_location_scope_untrusted")
        area = areas[sheet]
        area["source_cells"] += 1
        place = by_sheet[sheet]
        evidence = latest.get((sheet, cell))
        exact = (
            row.get("status") == "exact_name_and_unit_candidate"
            and place["status"] == "exact_name_candidate"
            and len(row.get("candidate_item_ids", [])) == 1
            and len(place["candidate_location_ids"]) == 1
        )
        valid = bool(exact and evidence
            and evidence.item_id == row["candidate_item_ids"][0]
            and evidence.location_id == place["candidate_location_ids"][0]
            and evidence.material_display == row.get("material")
            and evidence.form == row.get("form")
            and evidence.unit == row.get("unit"))
        if valid:
            area["evidence_cells"] += 1
        else:
            area["pending_cells"] += 1
        items.append({
            "sheet": sheet, "cell": cell,
            "material": row.get("material"), "unit": row.get("unit"),
            "mapping_status": row.get("status"),
            "evidence_status": "manager_evidence_current" if valid else "not_certified_for_source",
            "certification_version": evidence.version if valid else None,
        })

    total = len(items)
    evidence_count = sum(a["evidence_cells"] for a in areas.values())
    return {
        "business_date": business_date.isoformat(),
        "source_hash": report["source_hash"],
        "mode": "read_only_certification_coverage",
        "areas": list(areas.values()),
        "items": items,
        "summary": {
            "source_cells": total, "evidence_cells": evidence_count,
            "pending_cells": total - evidence_count,
            "warehouse_areas_with_exact_candidate": sum(
                by_sheet[s]["status"] == "exact_name_candidate" for s in SHEETS
            ),
            "total_warehouse_areas": len(SHEETS),
        },
        "mapping_activated": False, "ready_to_sign": False,
        "automatic_carry_forward_allowed": False,
        "inventory_writes": 0, "sheets_writes": 0,
    }
