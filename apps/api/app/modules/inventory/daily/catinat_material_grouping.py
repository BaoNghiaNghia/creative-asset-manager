"""Read-only grouped review of Catinat inventory material candidates.

Preserves every source row and evidence reference. Exact matches remain
proposals until an authorized manager explicitly approves them.
"""
from __future__ import annotations

from collections import defaultdict
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.daily.catinat_catalog_mapping import _normal
from app.modules.inventory.materials import (
    material_candidate_group_evidence_hash, normalize_material_text,
)
from app.modules.inventory.persistence_model import InventoryItemModel, InventoryMaterialCandidateModel

_EVIDENCE_HASH = re.compile(r"^[0-9a-f]{64}$")


def _review_evidence(row: InventoryMaterialCandidateModel) -> dict:
    context = row.context_json if isinstance(row.context_json, dict) else {}
    value = context.get("review_evidence")
    return value if isinstance(value, dict) else {}


def _unit_evidence(row: InventoryMaterialCandidateModel) -> list[dict]:
    values = _review_evidence(row).get("unit_evidence")
    if not isinstance(values, list):
        return []
    evidence = []
    for entry in values:
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
            continue
        value = entry["value"].strip()
        if not _normal(value):
            continue
        evidence.append({
            "value": value,
            "header": entry.get("header") if isinstance(entry.get("header"), str) else "",
            "value_cell": entry.get("value_cell") if isinstance(entry.get("value_cell"), str) else "",
            "header_cell": entry.get("header_cell") if isinstance(entry.get("header_cell"), str) else "",
            "evidence_hash": entry.get("evidence_hash") if isinstance(entry.get("evidence_hash"), str) else "",
        })
    return evidence


def _has_certifiable_units(row: InventoryMaterialCandidateModel, item: InventoryItemModel) -> tuple[bool, str]:
    review = _review_evidence(row)
    evidence = _unit_evidence(row)
    if review.get("status") != "explicit_unit_found" or not evidence:
        return False, "missing_unit_evidence"
    if len(evidence) != len(review.get("unit_evidence") or []):
        return False, "candidate_unit_evidence_invalid"
    allowed = {
        _normal(value) for value in (item.base_unit, item.whole_unit, item.fraction_unit)
        if isinstance(value, str) and _normal(value)
    }
    for unit in evidence:
        if not unit["value_cell"] or not unit["header_cell"] or not _EVIDENCE_HASH.fullmatch(unit["evidence_hash"]):
            return False, "incomplete_unit_evidence_reference"
        if _normal(unit["value"]) not in allowed:
            return False, "unit_not_in_approved_item_units"
    return True, "explicit_unit_evidence_matches_catalog"


def _source_row_view(row: InventoryMaterialCandidateModel) -> dict:
    review = _review_evidence(row)
    return {
        "candidate_id": row.id,
        "sheet": row.sheet,
        "row": row.source_row,
        "category": row.category,
        "name": row.raw_name,
        "status": row.status,
        "suggested_item_id": row.suggested_item_id,
        "suggested_canonical_name": row.suggested_canonical_name,
        "confidence": float(row.confidence or 0),
        "reasons": list(row.reasons_json or []),
        "name_cell": review.get("name_cell") if isinstance(review.get("name_cell"), str) else "",
        "unit_evidence": _unit_evidence(row),
    }


def grouped_material_candidates(session: Session, tenant_id: str) -> dict:
    records = list(session.scalars(select(InventoryMaterialCandidateModel).where(
        InventoryMaterialCandidateModel.tenant_id == tenant_id,
        InventoryMaterialCandidateModel.status.in_(("new_material", "possible_rename", "ambiguous")),
    )))
    catalog = list(session.scalars(select(InventoryItemModel).where(
        InventoryItemModel.tenant_id == tenant_id,
        InventoryItemModel.active.is_(True),
    )))
    by_normalized_name = defaultdict(list)
    by_exact_name = defaultdict(list)
    for item in catalog:
        by_normalized_name[normalize_material_text(item.name)].append(item)
        by_exact_name[_normal(item.name)].append(item)

    grouped = defaultdict(list)
    for record in records:
        grouped[normalize_material_text(record.raw_name) or _normal(record.raw_name)].append(record)

    entries = []
    for key, rows in sorted(grouped.items()):
        names = sorted({row.raw_name for row in rows})
        sheets = sorted({row.sheet for row in rows if row.sheet})
        units = sorted({
            evidence["value"] for row in rows for evidence in _unit_evidence(row)
        })
        normalized_matches = by_normalized_name.get(key, [])
        exact_name_keys = {_normal(row.raw_name) for row in rows}
        exact_item = None
        if len(exact_name_keys) == 1:
            exact_matches = by_exact_name.get(next(iter(exact_name_keys)), [])
            if len(exact_matches) == 1:
                exact_item = exact_matches[0]
        suggested_item = normalized_matches[0] if len(normalized_matches) == 1 else None
        all_units_match = bool(exact_item) and all(
            _has_certifiable_units(row, exact_item)[0] for row in rows
        )
        status = (
            "exact_name_and_evidence_unit_candidate" if all_units_match else
            "ambiguous_name" if len(normalized_matches) > 1 else
            "unit_review_required" if exact_item else "material_review_required"
        )

        approval_buckets = defaultdict(list)
        for row in rows:
            approval_buckets[(
                row.sheet, normalize_material_text(row.category),
                normalize_material_text(row.raw_name),
            )].append(row)
        approval_groups = []
        for (sheet, _category_key, _name_key), group_rows in sorted(
            approval_buckets.items(), key=lambda pair: (pair[0][0], pair[0][1], pair[0][2])
        ):
            seed = sorted(group_rows, key=lambda row: (row.source_row, row.id))[0]
            candidates = by_normalized_name.get(normalize_material_text(seed.raw_name), [])
            item = candidates[0] if len(candidates) == 1 else None
            exact_names = bool(item) and all(
                _normal(row.raw_name) == _normal(item.name) for row in group_rows
            )
            unit_results = [
                _has_certifiable_units(row, item) if item else (False, "no_unique_catalog_match")
                for row in group_rows
            ]
            conflicts = bool(item) and any(
                row.suggested_item_id and row.suggested_item_id != item.id
                for row in group_rows
            )
            if len(group_rows) > 100:
                blocker = "candidate_group_too_large"
            elif len(candidates) > 1:
                blocker = "duplicate_catalog_name"
            elif item is None:
                blocker = "no_unique_catalog_match"
            elif not exact_names:
                blocker = "candidate_name_variant_requires_review"
            elif conflicts:
                blocker = "conflicting_catalog_suggestion"
            elif not all(ok for ok, _ in unit_results):
                blocker = next(reason for ok, reason in unit_results if not ok)
            else:
                blocker = ""
            approval_groups.append({
                "candidate_id": seed.id,
                "candidate_ids": sorted(row.id for row in group_rows),
                "expected_count": len(group_rows),
                "evidence_hash": material_candidate_group_evidence_hash(group_rows, item) if item else "",
                "sheet": sheet,
                "category": seed.category,
                "name": seed.raw_name,
                "item_id": item.id if item else None,
                "item_name": item.name if item else None,
                "item_units": sorted({
                    value for value in (item.base_unit, item.whole_unit, item.fraction_unit)
                    if isinstance(value, str) and value
                }) if item else [],
                "can_bulk_approve": bool(item and not blocker),
                "blocker": blocker or None,
                "evidence_cells": sorted({
                    f"{sheet}!{evidence['value_cell']}"
                    for row in group_rows for evidence in _unit_evidence(row)
                    if evidence["value_cell"]
                }),
            })

        entries.append({
            "name": names[0],
            "aliases": names,
            "source_row_count": len(rows),
            "sheets": sheets,
            "evidence_units": units,
            "status": status,
            "suggested_item_id": suggested_item.id if suggested_item else None,
            "suggested_item_name": suggested_item.name if suggested_item else None,
            "candidate_item_ids": [exact_item.id] if all_units_match and exact_item else [],
            "certified": False,
            "source_rows": [
                _source_row_view(row) for row in sorted(rows, key=lambda item: (item.sheet, item.source_row, item.id))
            ],
            "approval_groups": approval_groups,
        })
    return {
        "source_rows": len(records),
        "unique_names": len(entries),
        "groups": entries,
        "automatic_approvals": 0,
        "inventory_writes": 0,
        "sheet_writes": 0,
    }
