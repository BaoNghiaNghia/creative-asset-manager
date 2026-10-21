"""Context-aware deterministic parser for Catinat's overloaded '+' entries.

Do not infer dates, batch time, shifts, unit conversion or a verified count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.modules.inventory.daily.catinat_policy import amount, InventoryPolicyBlocked


@dataclass(frozen=True)
class ParsedUsage:
    raw: str
    status: str
    morning: Decimal | None
    evening: Decimal | None
    total: Decimal | None
    variants: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True)
class ParsedContainers:
    raw: str
    status: str
    quantities: tuple[Decimal, ...]
    unit: str | None
    production_date_text: str | None = None
    prepared_at_verified: bool = False


_SHIFT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*\+\s*(\d+(?:[.,]\d+)?)\s*$")
_LEMON_VARIANTS = re.compile(
    r"^\s*(\d+)\s*hạt\s*\+\s*(\d+)\s*(?:ko|không)\s*hạt\s*$",
    flags=re.IGNORECASE,
)
_BOXES = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)(?:\s*\+\s*(\d+(?:[.,]\d+)?))\s*(miếng|miếnh|gr|g|ml)\s*$",
    flags=re.IGNORECASE,
)
_PREPARED = re.compile(r"^\s*(\d{1,2}/\d{1,2})\s*\(([^()]*)\)\s*$")


def parse_usage(raw: str) -> ParsedUsage:
    """Q18: plain E A+B means shifts; named lemon variants are not shifts."""
    original = str(raw or "")
    lemon = _LEMON_VARIANTS.fullmatch(original)
    if lemon:
        seeded, seedless = Decimal(lemon.group(1)), Decimal(lemon.group(2))
        return ParsedUsage(original, "variant_not_shifts", None, None, None,
                           (("seeded", seeded), ("seedless", seedless)))
    shifts = _SHIFT.fullmatch(original)
    if shifts:
        try:
            morning, evening = amount(shifts.group(1)), amount(shifts.group(2))
        except InventoryPolicyBlocked:
            pass
        else:
            return ParsedUsage(original, "two_shifts", morning, evening, morning + evening)
    return ParsedUsage(original, "needs_review", None, None, None)


def parse_stock_containers(raw: str) -> ParsedContainers:
    """Q08/Q11/Q36: dates and containers are evidence, not verified expiry."""
    original = str(raw or "")
    production_date = None
    body = original
    match = _PREPARED.fullmatch(original)
    if match:
        production_date, body = match.group(1), match.group(2)
    boxes = _BOXES.fullmatch(body)
    if not boxes:
        return ParsedContainers(original, "needs_review", (), None, production_date)
    try:
        left, right = amount(boxes.group(1)), amount(boxes.group(2))
    except InventoryPolicyBlocked:
        return ParsedContainers(original, "needs_review", (), None, production_date)
    unit = boxes.group(3).casefold()
    if unit == "miếnh":
        unit = "miếng"  # retain raw typo, normalize representation only
    if unit == "g":
        unit = "gr"
    return ParsedContainers(original, "quantities_unverified_container_identity",
                            (left, right), unit, production_date)
