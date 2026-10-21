"""Catinat evening physical-count readiness, strictly READ ONLY (Q02/Q03-05/Q27/Q34).

Inspects the three actual warehouse closing columns and the shift report.
This evidence is NOT a signed closing, approved catalog, validated expiration,
or authorization for Google Sheets writes/carry-forward.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
from typing import Any

from app.modules.inventory.daily.catinat_policy import InventoryPolicyBlocked, amount
from app.modules.inventory.daily_sheet.catinat_closing import SHEET as REPORT_TAB
from app.modules.inventory.daily_sheet.catinat_preview import (
    CatinatPreviewError, _metadata_size,
)

# 0-indexed source columns. The first three sets are opening/flows/interim
# related to a physical stock channel, including its actual closing column.
WAREHOUSES: dict[str, dict[str, tuple[int, ...] | tuple[str, int]]] = {
    "KHO PHA CHẾ": {
        "whole": (1, 5, 7, 11), "loose": (3, 6, 9, 12),
        "unit_columns": (8, 10), "closing_heading": ("Tồn cuối", 11),
    },
    "PHÒNG PHA CHẾ": {
        "whole": (1, 5, 7), "loose": (3, 6, 9),
        "unit_columns": (8, 10), "closing_heading": ("Tồn cuối", 7),
    },
    "KHO PHÒNG RANG": {
        "whole": (1, 5, 7, 9, 13), "loose": (3, 6, 8, 11, 14),
        "unit_columns": (10, 12), "closing_heading": ("Tồn cuối", 9),
    },
}
DATE_CELLS = {
    "KHO PHA CHẾ": 9, "PHÒNG PHA CHẾ": 9,
    "KHO PHÒNG RANG": 11, REPORT_TAB: 1,
}
_REPORT_PREPARED = re.compile(r"(?<!\d)\d{1,2}/\d{1,2}\s*\(")


def _text(row: list[Any], col: int) -> str:
    return str(row[col]).strip() if col < len(row) and row[col] is not None else ""


def _stock_channel(item: str, row_number: int, row: list[Any], *, form: str,
                   fields: tuple[int, ...], unit_index: int,
                   closing: int) -> dict | None:
    # Closing positions are L/M (KHO PHA CHẾ), H/J (PHÒNG PHA CHẾ),
    # J/L (KHO PHÒNG RANG). The latter has waste AFTER the closing cells.
    # A configured physical-stock unit means this channel needs an explicit
    # physical count even when ALL quantities are blank (Q02/Q27). Never
    # silently treat an unused-looking, named inventory row as zero.
    unit = _text(row, unit_index)
    active = bool(unit) or any(_text(row, col) for col in fields)
    if not active:
        return None
    raw = _text(row, closing)
    cell = f"{chr(65 + closing)}{row_number}"
    unit = _text(row, unit_index)
    try:
        quantity = amount(raw, zero_slash=True)
        parsed = format(quantity.normalize(), "f")
        state = "physical_value_present_unconfirmed"
        blocker = "employee_physical_confirmation_required"
    except InventoryPolicyBlocked:
        state = "invalid_or_missing_physical"
        blocker = "physical_count_missing_or_ambiguous"
        parsed = None
    return {
        "sheet": item, "row": row_number, "form": form, "cell": cell,
        "unit": unit, "raw": raw, "quantity": parsed,
        "status": state, "blocker": blocker,
    }


def _day(cell_value: str) -> date:
    try:
        return datetime.strptime(cell_value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise CatinatPreviewError("catinat_business_day_unverifiable") from exc


def serialize_evening_readiness(
    *, spreadsheet_id: str, business_date: date,
    ranges: dict[str, list[list[Any]]],
) -> dict:
    required = set(WAREHOUSES) | {REPORT_TAB}
    if set(ranges) != required:
        raise CatinatPreviewError("catinat_required_tabs_missing")
    blockers: list[dict[str, Any]] = []
    records: list[dict] = []
    for sheet in (*WAREHOUSES, REPORT_TAB):
        rows = ranges[sheet]
        if len(rows) < 5:
            raise CatinatPreviewError("catinat_tab_truncated")
        pos = DATE_CELLS[sheet]
        actual_date = _day(_text(rows[2 if sheet == REPORT_TAB else 0], pos))
        if actual_date != business_date:
            raise CatinatPreviewError("catinat_business_date_mismatch")
        if sheet == REPORT_TAB:
            if _text(rows[4], 0) != "STT" or "Nguyên Liệu" not in _text(rows[4], 1):
                raise CatinatPreviewError("catinat_report_layout_changed")
            # Date-only timestamps in prepared-product expressions cannot
            # establish a 96-hour expiration under Q40.
            for num, row in enumerate(rows[6:], start=7):
                if any(_REPORT_PREPARED.search(str(value or "")) for value in row[3:11]):
                    blockers.append({
                        "sheet": sheet, "row": num,
                        "code": "prepared_time_and_expiry_require_staff_verification",
                    })
            continue
        columns = WAREHOUSES[sheet]
        heading, heading_idx = columns["closing_heading"]
        if _text(rows[2], heading_idx).casefold() != heading.casefold():
            raise CatinatPreviewError("catinat_closing_column_layout_changed")
        if _text(rows[3], 0):
            raise CatinatPreviewError("catinat_warehouse_header_misaligned")
        for row_number, row in enumerate(rows[4:], start=5):
            name = _text(row, 0)
            if not name:
                if any(_text(row, col) for form in ("whole", "loose") for col in columns[form]):
                    blockers.append({"sheet": sheet, "row": row_number,
                                     "code": "unmapped_quantity_row"})
                continue
            units = columns["unit_columns"]
            for index, form in enumerate(("whole", "loose")):
                record = _stock_channel(
                    sheet, row_number, row, form=form,
                    fields=columns[form], unit_index=units[index],
                    closing=(
                        (11, 12) if sheet == "KHO PHA CHẾ" else
                        (7, 9) if sheet == "PHÒNG PHA CHẾ" else (9, 11)
                    )[index],
                )
                if record is None:
                    continue
                record["material"] = name
                records.append(record)
                if not record["unit"]:
                    blockers.append({
                        "sheet": sheet, "row": row_number, "cell": record["cell"],
                        "code": "stock_unit_missing",
                    })
                blockers.append({
                    "sheet": sheet, "row": row_number,
                    "cell": record["cell"], "form": form,
                    "code": record["blocker"],
                })
    if not records:
        blockers.append({"code": "no_physical_counts_discovered"})
    # Hash ALL observed cells including dates/header/flows, not only parsed
    # closing quantities, to detect stale preflight after any staff edit.
    serialized = json.dumps({
        "spreadsheet_id": spreadsheet_id, "business_date": business_date.isoformat(),
        "ranges": ranges,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "business_date": business_date.isoformat(),
        "spreadsheet_id": spreadsheet_id,
        "shift": "evening",
        "mode": "read_only_not_a_signed_close",
        "source_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "items": records, "blockers": blockers,
        "staff_confirmation_available": False,
        "automatic_carry_forward_allowed": False,
        "sheets_writes": 0,
    }


def load_evening_readiness(
    tenant_id: str, business_date: date, *, sessions,
    client_factory=None, token_resolver=None,
) -> dict:
    """Read only the configured tenant workbook; never accept a client file ID."""
    from app.modules.inventory.daily_sheet.catinat_preview import (
        GoogleSheetsInventoryClient, get_connection_access_token,
    )
    from app.modules.assets.model import ExternalSourceModel
    from app.modules.inventory.persistence_model import InventorySettingsModel
    from sqlalchemy import select
    import asyncio

    client_factory = client_factory or GoogleSheetsInventoryClient
    token_resolver = token_resolver or get_connection_access_token
    with sessions() as session:
        settings = session.scalar(select(InventorySettingsModel).where(
            InventorySettingsModel.tenant_id == tenant_id
        ))
        if settings is None or not settings.daily_working_spreadsheet_file_id:
            raise CatinatPreviewError("catinat_workbook_not_configured")
        source = session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == settings.external_source_id,
        ))
        oauth_id = str(((source.source_metadata or {}) if source else {}).get("oauth_connection_id") or "")
        if not oauth_id:
            raise CatinatPreviewError("catinat_google_connection_missing")
        spreadsheet_id = str(settings.daily_working_spreadsheet_file_id)
    token = token_resolver(oauth_id)
    if hasattr(token, "__await__"):
        token = asyncio.run(token)
    with client_factory(str(token)) as google:
        metadata = google.spreadsheet_metadata(spreadsheet_id)
        # Reuse existing report layout validation.
        _metadata_size(metadata)
        props = [s.get("properties") or {} for s in metadata.get("sheets", [])]
        required = set(WAREHOUSES) | {REPORT_TAB}
        if any(sum(p.get("title") == name for p in props) != 1 for name in required):
            raise CatinatPreviewError("catinat_required_tabs_missing")
        limits = {}
        for sheet in required:
            s = next(p for p in props if p.get("title") == sheet)
            count = int((s.get("gridProperties") or {}).get("rowCount") or 0)
            if count < 5 or count > 500:
                raise CatinatPreviewError("catinat_tab_size_unverified")
            limits[sheet] = count
        ordered = [*WAREHOUSES, REPORT_TAB]
        areas = [
            f"'{name}'!A1:{'K' if name == REPORT_TAB else 'O'}{limits[name]}"
            for name in ordered
        ]
        blocks = google.batch_get_values(
            spreadsheet_id, areas, value_render_option="FORMATTED_VALUE",
        )
    if len(blocks) != len(areas):
        raise CatinatPreviewError("catinat_multi_tab_read_incomplete")
    ranges = {key: list(block.get("values") or []) for key, block in zip(ordered, blocks)}
    return serialize_evening_readiness(
        spreadsheet_id=spreadsheet_id, business_date=business_date, ranges=ranges,
    )
