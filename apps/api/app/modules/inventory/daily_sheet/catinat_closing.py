"""Read-only, deterministic closing suggestions for Catinat's shift report.

No Google mutation is performed here. The caller must still validate source
identity, date, writable cell, freshness, and independent readback before any
separately authorized live write. Not a physical stock-count certification.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Sequence

from app.modules.inventory.daily_sheet.catinat_semantics import parse_usage

SHEET = "Báo cáo sử dụng NVL trong ca"
EXPECTED_HEADERS = {
    0: "STT",
    1: "Tên Nguyên Liệu / Vật Tư",
    3: "SL Đầu Ca / Nhận",
    4: "SL Sử Dụng Pha Chế",
    6: "Nhập Hàng",
    7: "SL Huỷ / Hư Hỏng",
    10: "Tồn Cuối Ca",
}
NUMERIC_RE = re.compile(r"^[0-9]+(?:[,.][0-9]+)?$")


class CatinatClosingLayoutError(ValueError):
    """Cannot establish that the cells belong to the approved Catinat report."""


@dataclass(frozen=True)
class ClosingSuggestion:
    sheet: str
    row: int
    item: str
    target: str
    computed_value: str
    inputs: dict[str, str]
    evidence_hash: str
    status: str = "computed_unverified"


@dataclass(frozen=True)
class ClosingBlocked:
    row: int
    item: str
    reason: str


@dataclass(frozen=True)
class ClosingPreview:
    business_date: date
    suggestions: tuple[ClosingSuggestion, ...]
    blocked: tuple[ClosingBlocked, ...]


def _entered(cell: Any) -> Any:
    if isinstance(cell, dict):
        entered = cell.get("userEnteredValue")
        if isinstance(entered, dict):
            for key in ("formulaValue", "stringValue", "numberValue", "boolValue"):
                if key in entered:
                    if key == "formulaValue":
                        return "=" + str(entered[key]).lstrip("=")
                    return entered[key]
            return None
        if "raw" in cell:
            return cell["raw"]
        return cell.get("value")
    return cell


def _display(cell: Any) -> str:
    if isinstance(cell, dict) and "formattedValue" in cell:
        return str(cell["formattedValue"] or "").strip()
    entered = _entered(cell)
    return "" if entered is None else str(entered).strip()


def _cell(row: Sequence[Any], col: int) -> Any:
    return row[col] if len(row) > col else None


def _numeric(cell: Any, *, stock_slash_is_zero: bool = False) -> Decimal | None:
    raw = _entered(cell)
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (float, int, Decimal)):
        if isinstance(cell, dict):
            fmt = (cell.get("userEnteredFormat") or {}).get("numberFormat") or {}
            if str(fmt.get("type", "")).upper() in ("DATE", "DATE_TIME", "TIME"):
                return None
            if re.search(r"d[/.-]m|m[/.-]d|yyyy", str(fmt.get("pattern", "")), re.I):
                return None
        try:
            result = Decimal(str(raw))
        except InvalidOperation:
            return None
        return result if result.is_finite() and result >= 0 else None
    text = str(raw).strip()
    if text == "/" and stock_slash_is_zero:
        return Decimal(0)
    if not NUMERIC_RE.fullmatch(text):
        return None
    # Plain 1,234 is ambiguous between 1234 and 1.234; fail closed.
    if "," in text and len(text.split(",")[1]) == 3:
        return None
    try:
        result = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _numeric_or_blank(
    cell: Any, *, assume_blank_movement_zero: bool,
) -> Decimal | None:
    if not _display(cell) and _entered(cell) in (None, ""):
        return Decimal(0) if assume_blank_movement_zero else None
    return _numeric(cell)


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def plan_catinat_missing_closings(
    rows: Sequence[Sequence[Any]],
    *,
    business_date: date,
    assume_blank_movement_zero: bool = False,
) -> ClosingPreview:
    """Plan only blank K cells for simple, numeric, single-dimension rows.

    The default blocks blank movement evidence. An owner-approved policy can
    treat missing consumption/inbound/waste as zero; do not confuse this with
    a physically verified closing count. K is never overwritten when filled.
    """
    if len(rows) < 6:
        raise CatinatClosingLayoutError("report_range_too_short")
    for index, expected in EXPECTED_HEADERS.items():
        if _display(_cell(rows[4], index)).casefold() != expected.casefold():
            raise CatinatClosingLayoutError("shift_report_headers_changed")
    reported_day = _display(_cell(rows[2], 1))
    try:
        sheet_date = datetime.strptime(reported_day, "%d/%m/%Y").date()
    except ValueError as exc:
        raise CatinatClosingLayoutError("report_date_unverifiable") from exc
    if sheet_date != business_date:
        raise CatinatClosingLayoutError("report_business_date_mismatch")

    suggestions: list[ClosingSuggestion] = []
    blocked: list[ClosingBlocked] = []
    for row_number in range(7, len(rows) + 1):
        row = rows[row_number - 1]
        ordinal, name = _display(_cell(row, 0)), _display(_cell(row, 1))
        if not ordinal.isdigit() or not name:
            continue
        target = _cell(row, 10)
        if _display(target) or _entered(target) not in (None, ""):
            continue  # Respect an operator's existing count, slash or formula.
        # F and I are mass/volume consumption/waste dimensions; never add
        # them to count quantities without an approved conversion factor.
        if _display(_cell(row, 5)) or _display(_cell(row, 8)):
            blocked.append(ClosingBlocked(row_number, name, "mixed_quantity_dimensions"))
            continue
        # These exact header/column mappings are from the Catinat operator sheet.
        opening = _numeric(_cell(row, 3), stock_slash_is_zero=True)
        usage = _numeric_or_blank(_cell(row, 4), assume_blank_movement_zero=assume_blank_movement_zero)
        if usage is None:
            # Q18: E=8+4 means 8 used AM and 4 used PM. Named variants
            # (e.g. 4 hạt+2 ko hạt) MUST NOT be treated as AM+PM.
            shift_usage = parse_usage(_display(_cell(row, 4)))
            if shift_usage.status == "two_shifts":
                usage = shift_usage.total
        incoming = _numeric_or_blank(_cell(row, 6), assume_blank_movement_zero=assume_blank_movement_zero)
        waste = _numeric_or_blank(_cell(row, 7), assume_blank_movement_zero=assume_blank_movement_zero)
        if opening is None or usage is None or incoming is None or waste is None:
            blocked.append(ClosingBlocked(row_number, name, "missing_or_ambiguous_numeric_evidence"))
            continue
        value = opening + incoming - usage - waste
        if value < 0:
            blocked.append(ClosingBlocked(row_number, name, "negative_expected_closing"))
            continue
        evidence = {key: _display(_cell(row, col)) for key, col in
                    (("D", 3), ("E", 4), ("F", 5), ("G", 6), ("H", 7), ("I", 8), ("K", 10))}
        digest = hashlib.sha256(
            json.dumps({"sheet": SHEET, "date": business_date.isoformat(),
                        "row": row_number, "item": name, "cells": evidence},
                       ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        suggestions.append(ClosingSuggestion(
            sheet=SHEET, row=row_number, item=name, target="K" + str(row_number),
            computed_value=_fmt(value), inputs=evidence, evidence_hash=digest,
        ))
    return ClosingPreview(
        business_date=business_date, suggestions=tuple(suggestions),
        blocked=tuple(blocked),
    )
