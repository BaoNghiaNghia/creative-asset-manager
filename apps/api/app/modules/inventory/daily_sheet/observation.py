"""Read-only observations of operator-entered Vietnamese inventory quantities.

No inference from blank, "/" or mixed batch expressions. This classifier is
for diagnostics and golden fixtures; it never grants permission to write Sheets
or decides a physical stock adjustment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_SIMPLE = re.compile(r"(?P<value>[0-9]+(?:[.,][0-9]+)?)\s*(?P<unit>gr|g|ml|chai|can|lon|hộp|gói|bịch|túi|cái|trái)?\Z", re.I)
_FRACTION = re.compile(r"(?:(?P<whole>[0-9]+)\s*\+\s*)?(?P<num>[0-9]+)\s*/\s*(?P<den>[0-9]+)\s*(?P<unit>chai|can|hộp|gói|bịch|túi|trái)\Z", re.I)


@dataclass(frozen=True, slots=True)
class QuantityObservation:
    status: str
    raw: str
    quantity: Decimal | None
    unit: str | None
    requires_review: bool
    reason: str


def observe_quantity(
    value: Any, *,
    number_format: str | None = None,
    expected_unit: str | None = None,
) -> QuantityObservation:
    """Separate parseable observations from evidence requiring operator review.

    Excel may store "1/2 chai" as serial 46054 with a date cell format.
    If there is date-style formatting in a quantity column, the original
    user entry cannot be recovered from a serial alone: refuse conversion.
    """
    raw = "" if value is None else str(value)
    text_value = raw.strip()
    def result(status: str, *, amount: Decimal | None = None,
               unit: str | None = None, reason: str = "") -> QuantityObservation:
        return QuantityObservation(status, raw, amount, unit, status != "PARSED", reason)

    fmt = (number_format or "").strip().lower()
    if fmt and ("d/" in fmt or "m/" in fmt or "yy" in fmt or "dd" in fmt):
        return result("REVIEW_REQUIRED", reason="excel_date_formatted_quantity")
    if not text_value:
        return result("MISSING_DATA", reason="blank_is_not_zero")
    if text_value == "/":
        return result("REVIEW_REQUIRED", reason="slash_meaning_unconfirmed")
    if isinstance(value, bool):
        return result("REVIEW_REQUIRED", reason="boolean_is_not_stock")
    if text_value.startswith("="):
        return result("REVIEW_REQUIRED", reason="formula_not_operator_count")
    if any(marker in text_value for marker in ("(", ")", ";")):
        return result("REVIEW_REQUIRED", reason="lot_or_damage_notation")
    if "+" in text_value and not _FRACTION.fullmatch(text_value):
        return result("REVIEW_REQUIRED", reason="unverified_quantity_expression")

    fraction = _FRACTION.fullmatch(text_value)
    if fraction:
        denominator = int(fraction["den"])
        if not denominator:
            return result("REVIEW_REQUIRED", reason="invalid_fraction")
        amount = Decimal(int(fraction["num"])) / Decimal(denominator)
        if fraction["whole"]:
            amount += Decimal(fraction["whole"])
        unit = fraction["unit"].lower()
        if expected_unit and expected_unit.casefold() != unit.casefold():
            return result("REVIEW_REQUIRED", reason="unit_mismatch")
        # Exact fractional package count is observable, but the exact
        # package->gram conversion (and stock eligibility) is not approved.
        return result("REVIEW_REQUIRED", amount=amount, unit=unit,
                      reason="fractional_package_requires_review")

    match = _SIMPLE.fullmatch(text_value)
    if not match:
        return result("REVIEW_REQUIRED", reason="unrecognized_quantity_grammar")
    try:
        amount = Decimal(match["value"].replace(",", "."))
    except InvalidOperation:
        return result("REVIEW_REQUIRED", reason="invalid_quantity")
    unit = (match["unit"] or expected_unit or "count").lower()
    if unit == "gr":
        unit = "g"
    if expected_unit and (expected_unit.lower().replace("gr", "g") != unit):
        return result("REVIEW_REQUIRED", amount=amount, unit=unit,
                      reason="unit_mismatch")
    return result("PARSED", amount=amount, unit=unit, reason="exact_quantity")
