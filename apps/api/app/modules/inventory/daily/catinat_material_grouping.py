"""Read-only grouped review of Catinat inventory material candidates.

Preserves every source row. A match is a candidate, not an approved mapping.
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.daily.catinat_catalog_mapping import _normal
from app.modules.inventory.persistence_model import InventoryItemModel, InventoryMaterialCandidateModel


def grouped_material_candidates(session: Session, tenant_id: str) -> dict:
    records = list(session.scalars(select(InventoryMaterialCandidateModel).where(
        InventoryMaterialCandidateModel.tenant_id == tenant_id,
        InventoryMaterialCandidateModel.status.in_(("new_material", "possible_rename", "ambiguous")),
    )))
    catalog = list(session.scalars(select(InventoryItemModel).where(
        InventoryItemModel.tenant_id == tenant_id,
        InventoryItemModel.active.is_(True),
    )))
    by_name = defaultdict(list)
    for item in catalog:
        by_name[_normal(item.name)].append(item)
    grouped = defaultdict(list)
    for record in records:
        grouped[_normal(record.raw_name)].append(record)
    entries = []
    for key, rows in sorted(grouped.items()):
        names = sorted({row.raw_name for row in rows})
        units = set()
        sheets = set()
        for row in rows:
            if row.sheet:
                sheets.add(row.sheet)
            context = row.context_json if isinstance(row.context_json, dict) else {}
            review = context.get("review_evidence")
            if not isinstance(review, dict) or review.get("status") != "explicit_unit_found":
                continue
            for evidence in review.get("unit_evidence", []):
                if isinstance(evidence, dict) and isinstance(evidence.get("value"), str):
                    value = _normal(evidence["value"])
                    if value:
                        units.add(value)
        exact = by_name.get(key, [])
        match = exact[0] if len(exact) == 1 else None
        approved_units = (
            {_normal(u) for u in (match.base_unit, match.whole_unit, match.fraction_unit) if u}
            if match else set()
        )
        unit_match = bool(match and units and units.issubset(approved_units))
        status = (
            "exact_name_and_evidence_unit_candidate" if unit_match else
            "ambiguous_name" if len(exact) > 1 else
            "unit_review_required" if match else "material_review_required"
        )
        entries.append({
            "name": names[0], "aliases": names, "source_row_count": len(rows),
            "sheets": sorted(sheets), "evidence_units": sorted(units),
            "status": status, "candidate_item_ids": [match.id] if unit_match else [],
            "certified": False,
        })
    return {
        "source_rows": len(records), "unique_names": len(entries),
        "groups": entries, "automatic_approvals": 0, "inventory_writes": 0,
        "sheet_writes": 0,
    }
