"""Strict, read-only numerical gate for verified Closing -> Opening evidence.

An arithmetic interpretation does not prove the workbook's physical stock
identity, unit or date. The callers must independently validate those details,
immutable source evidence and write permissions.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

_DECIMAL_TEXT = re.compile(r"[0-9]+(?:[.,][0-9]+)?\Z")
_AMBIGUOUS_GROUPING = re.compile(r"[0-9]{1,3},[0-9]{3}\Z")


def parse_verified_closing_number(value: Any) -> Decimal:
    """Refuse missing, nonfinite, negative, grouped or ambiguous stock counts.

    A bare \"1,234\" could mean 1.234 or 1234 depending on locale. It
    cannot be used for an automatic Morning Reset without operator evidence.
    Positive/zero numeric API values, unambiguous dot decimal and Vietnamese
    decimal comma are supported, without silently stripping punctuation.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("missing_closing_evidence")
    if isinstance(value, bool):
        raise ValueError("non_numeric_closing_evidence")
    if isinstance(value, str):
        candidate = value.strip()
        if _AMBIGUOUS_GROUPING.fullmatch(candidate):
            raise ValueError("ambiguous_closing_number_format")
        if not _DECIMAL_TEXT.fullmatch(candidate):
            raise ValueError("non_numeric_closing_evidence")
        candidate = candidate.replace(",", ".")
    elif isinstance(value, (int, float, Decimal)):
        candidate = str(value)
    else:
        raise ValueError("non_numeric_closing_evidence")
    try:
        amount = Decimal(candidate)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("non_numeric_closing_evidence") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("non_numeric_closing_evidence")
    return amount
