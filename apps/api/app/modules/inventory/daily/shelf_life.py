"""Pure Catinat shelf-life decision helpers (Q07/Q08/Q19/Q37/Q40/Q45).

This module NEVER changes inventory counts or writes to Google Sheets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PREPARED_MAX_AGE = timedelta(hours=96)


class ShelfLifeState(str, Enum):
    VERIFIED = "verified"
    MISSING_PREPARATION_TIME = "missing_preparation_time"
    UNKNOWN_OPENED_TIME = "unknown_opened_time"
    MISSING_PRODUCT_POLICY = "missing_product_policy"


@dataclass(frozen=True)
class ShelfLifeDecision:
    state: ShelfLifeState
    expires_at: datetime | None
    usable: bool
    reason: str


def _business_time(timestamp: datetime | None) -> datetime | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return timestamp.astimezone(BUSINESS_TZ)


def prepared_expiry(
    prepared_at: datetime | None,
    *,
    shorter_safety_deadline: datetime | None = None,
    as_of: datetime,
) -> ShelfLifeDecision:
    """Q40: exact 96 hours, NEVER four calendar days or guessed time-of-day."""
    now = _business_time(as_of)
    prepared = _business_time(prepared_at)
    safety = _business_time(shorter_safety_deadline)
    if prepared is None:
        return ShelfLifeDecision(ShelfLifeState.MISSING_PREPARATION_TIME, None, False, "preparation_time_unverified")
    deadline = prepared + PREPARED_MAX_AGE
    if safety is not None:
        deadline = min(deadline, safety)
    return ShelfLifeDecision(ShelfLifeState.VERIFIED, deadline, now < deadline, "within_shelf_life" if now < deadline else "expired")


def opened_product_expiry(
    opened_at: datetime | None,
    *,
    approved_open_duration: timedelta | None,
    package_expiry: datetime | None,
    as_of: datetime,
) -> ShelfLifeDecision:
    """Q37/Q45: unverified opened timestamp must quarantine, not guess."""
    now = _business_time(as_of)
    opened = _business_time(opened_at)
    packaging = _business_time(package_expiry)
    if opened is None:
        return ShelfLifeDecision(ShelfLifeState.UNKNOWN_OPENED_TIME, None, False, "quarantine_unverified_opening")
    if approved_open_duration is None or approved_open_duration <= timedelta(0):
        return ShelfLifeDecision(ShelfLifeState.MISSING_PRODUCT_POLICY, None, False, "approved_open_duration_required")
    deadline = opened + approved_open_duration
    if packaging is not None:
        deadline = min(deadline, packaging)
    return ShelfLifeDecision(ShelfLifeState.VERIFIED, deadline, now < deadline, "within_shelf_life" if now < deadline else "expired")
