"""Q31 read-only evidence audit for whole/loose stock unit conversion.

Existing InventoryItemModel.conversion_factor is *not* a manager approval.
A conversion remains informational evidence until a separate stock ledger
and signed shift-close protocol are implemented.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, _key,
)
from app.modules.inventory.daily.catinat_catalog_mapping import _normal
from app.modules.inventory.materials import normalize_material_text
from app.modules.inventory.persistence_model import (
    InventoryItemModel, InventoryMaterialPackageConversionModel,
)

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SHEETS = frozenset(("KHO PHA CHẾ", "PHÒNG PHA CHẾ", "KHO PHÒNG RANG"))


def read_only_conversion_review(
    sessions: sessionmaker[Session], *, tenant_id: str, business_date: date,
    report: dict,
) -> dict:
    """Return strictly tenant-scoped unit evidence for current read-only rows."""
    if (report.get("mode") != "read_only_uncertified_catalog_candidates"
            or report.get("business_date") != business_date.isoformat()
            or not isinstance(report.get("spreadsheet_id"), str)
            or not report["spreadsheet_id"]
            or not isinstance(report.get("source_hash"), str)
            or not _FINGERPRINT.fullmatch(report["source_hash"])
            or not isinstance(report.get("items"), list)):
        raise CatinatCountBlocked("conversion_preview_untrusted")
    rows = report["items"]
    try:
        keys = [_key(row) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise CatinatCountBlocked("conversion_preview_untrusted") from exc
    if len(keys) != len(set(keys)) or any(sheet not in _ALLOWED_SHEETS for sheet, _ in keys):
        raise CatinatCountBlocked("conversion_preview_duplicate_or_unknown_cell")
    candidate_ids = {
        item_id
        for row in rows
        if row.get("status") == "exact_name_and_unit_candidate"
        and isinstance(row.get("candidate_item_ids"), list)
        and len(row["candidate_item_ids"]) == 1
        for item_id in row["candidate_item_ids"]
        if isinstance(item_id, str) and item_id
    }
    with sessions() as session:
        materials = {m.id: m for m in session.scalars(
            select(InventoryItemModel).where(
                InventoryItemModel.tenant_id == tenant_id,
                InventoryItemModel.active.is_(True),
                InventoryItemModel.id.in_(candidate_ids),
            )
        )} if candidate_ids else {}
        conversions = list(session.scalars(
            select(InventoryMaterialPackageConversionModel).where(
                InventoryMaterialPackageConversionModel.tenant_id == tenant_id,
                InventoryMaterialPackageConversionModel.item_id.in_(materials.keys()),
            )
        )) if materials else []

    by_package = {
        (record.item_id, record.normalized_package): record
        for record in conversions
    }
    results = []
    counts: dict[str, int] = {}
    for row in rows:
        sheet, cell = _key(row)
        ids = row.get("candidate_item_ids")
        form, sheet_unit = row.get("form"), row.get("unit")
        item = materials.get(ids[0]) if (row.get("status") == "exact_name_and_unit_candidate"
            and isinstance(ids, list) and len(ids) == 1
            and isinstance(ids[0], str)) else None
        factor: str | None = None
        unit: str | None = None
        if not item or not isinstance(sheet_unit, str) or not isinstance(form, str):
            status = "catalog_mapping_requires_review"
        elif form == "loose":
            expected = item.fraction_unit or item.base_unit
            status = ("loose_unit_matches_catalog" if _normal(sheet_unit) == _normal(expected)
                      else "loose_unit_mismatch")
        elif form != "whole" or _normal(sheet_unit) != _normal(item.whole_unit or ""):
            status = "whole_unit_mismatch"
        elif _normal(item.whole_unit) == _normal(item.fraction_unit or item.base_unit):
            status = "conversion_not_needed_same_unit"
        else:
            record = by_package.get((item.id, normalize_material_text(sheet_unit)))
            target_unit = item.fraction_unit or item.base_unit
            if record is None:
                status = "approved_package_conversion_missing"
            elif not record.approved_by or not record.approved_by.strip():
                status = "package_conversion_not_approved"
            elif (_normal(record.canonical_unit) != _normal(target_unit)
                  or record.canonical_value is None
                  or not record.canonical_value.is_finite()
                  or record.canonical_value <= Decimal(0)):
                status = "approved_conversion_unit_or_factor_invalid"
            elif record.canonical_value != item.conversion_factor:
                # Different values in the existing item and manager evidence
                # are never silently reconciled into an authoritative factor.
                status = "approved_conversion_disagrees_with_catalog"
            else:
                status = "approved_conversion_evidence_found"
                factor = format(record.canonical_value, "f")
                unit = record.canonical_unit
        counts[status] = counts.get(status, 0) + 1
        results.append({
            "sheet": sheet, "cell": cell, "material": row.get("material"),
            "form": form, "source_unit": sheet_unit,
            "status": status, "approved_factor_evidence": factor,
            "approved_target_unit_evidence": unit,
            "conversion_applied": False,
        })
    return {
        "business_date": business_date.isoformat(), "source_hash": report["source_hash"],
        "mode": "read_only_package_conversion_evidence",
        "items": results,
        "summary": {"total_cells": len(results), "by_status": dict(sorted(counts.items())),
                    "approved_conversion_evidence_cells": counts.get(
                        "approved_conversion_evidence_found", 0)},
        "conversion_applied": False, "ready_to_sign": False,
        "automatic_carry_forward_allowed": False,
        "inventory_writes": 0, "sheets_writes": 0,
    }
