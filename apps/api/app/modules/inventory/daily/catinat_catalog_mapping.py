"""Read-only, tenant-bound Catinat Sheet to Inventory catalog candidate report.

Exact Unicode-normalized name and unit matches are review *candidates*, never
verified mapping, material conversion, stock posting or closing authority.
No guesses from fuzzy matches, SKUs, aliases, missing units or other tenants.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
import unicodedata
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, _key, _source,
)
from app.modules.inventory.persistence_model import InventoryItemModel, InventoryLocationModel

_WHITESPACE = re.compile(r"\s+")


def _normal(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value).strip()).casefold()


def _unit_match(catalog: InventoryItemModel, form: str, source_unit: str) -> bool:
    if form == "whole":
        approved_unit = catalog.whole_unit
    elif form == "loose":
        approved_unit = catalog.fraction_unit or catalog.base_unit
    else:
        return False
    return bool(_normal(approved_unit)) and _normal(source_unit) == _normal(approved_unit)


class CatinatCatalogMappingPreview:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def inspect(self, *, tenant_id: str, business_date: date,
                current_readiness: Callable[[], dict]) -> dict:
        ready = current_readiness()
        digest, spreadsheet = _source(ready, business_date)
        source_rows = ready["items"]
        # Even this read-only preview must fail closed on repeated or malformed
        # cell identity; a duplicated spreadsheet row cannot be silently accepted.
        identities = [_key(row) for row in source_rows]
        if len(identities) != len(set(identities)):
            raise CatinatCountBlocked("duplicate_source_count_identity")
        with self.sessions() as session:
            items = session.scalars(select(InventoryItemModel).where(
                InventoryItemModel.tenant_id == tenant_id,
                InventoryItemModel.active.is_(True),
            )).all()
            locations = session.scalars(select(InventoryLocationModel).where(
                InventoryLocationModel.tenant_id == tenant_id,
                InventoryLocationModel.active.is_(True),
            )).all()

        items_by_name: dict[str, list[InventoryItemModel]] = defaultdict(list)
        for item in items:
            if _normal(item.name):
                items_by_name[_normal(item.name)].append(item)
        locations_by_name: dict[str, list[InventoryLocationModel]] = defaultdict(list)
        for location in locations:
            if _normal(location.name):
                locations_by_name[_normal(location.name)].append(location)

        location_rows = []
        locations_status = {}
        for sheet in ("KHO PHA CHẾ", "PHÒNG PHA CHẾ", "KHO PHÒNG RANG"):
            matches = locations_by_name.get(_normal(sheet), [])
            status = ("no_exact_location" if not matches else
                      "ambiguous_location" if len(matches) > 1 else
                      "exact_name_candidate")
            locations_status[sheet] = status
            location_rows.append({
                "sheet": sheet, "status": status,
                "candidate_location_ids": sorted(x.id for x in matches),
            })

        rows = []
        counts: dict[str, int] = defaultdict(int)
        for source in source_rows:
            sheet, cell = _key(source)
            name = source.get("material")
            form = source.get("form")
            unit = source.get("unit")
            if not isinstance(name, str) or not isinstance(form, str) or not isinstance(unit, str):
                raise CatinatCountBlocked("invalid_catalog_source_fields")
            candidates = items_by_name.get(_normal(name), [])
            if not _normal(name):
                status = "material_name_missing"
            elif not candidates:
                status = "no_exact_item"
            elif len(candidates) != 1:
                status = "ambiguous_item"
            elif not _normal(unit):
                status = "source_unit_missing"
            elif not _unit_match(candidates[0], form, unit):
                status = "unit_mismatch"
            elif locations_status[sheet] != "exact_name_candidate":
                status = "location_requires_review"
            else:
                status = "exact_name_and_unit_candidate"
            counts[status] += 1
            rows.append({
                "sheet": sheet, "cell": cell, "material": name,
                "form": form, "unit": unit, "status": status,
                "candidate_item_ids": sorted(x.id for x in candidates),
                "location_status": locations_status[sheet],
            })
        candidates_count = counts.get("exact_name_and_unit_candidate", 0)
        return {
            "business_date": business_date.isoformat(), "spreadsheet_id": spreadsheet,
            "shift": "evening", "source_hash": digest,
            "mode": "read_only_uncertified_catalog_candidates",
            "catalog_coverage": {
                "active_item_count": len(items), "active_location_count": len(locations),
                "certified": False,
            },
            "locations": location_rows, "items": rows,
            "summary": {
                "total": len(rows), "exact_candidates": candidates_count,
                "requires_review": len(rows) - candidates_count,
                "by_status": dict(sorted(counts.items())),
            },
            "ready_to_sign": False, "automatic_carry_forward_allowed": False,
            "inventory_writes": 0, "sheets_writes": 0,
        }