"""Absolute age policy for disposable R2 video cache objects.

Expiry is pinned to the original READY cache timestamp; last_accessed_at and
derived playback preparation never extend it. This does not apply to source
videos in Drive or to the unrelated Etsy image prefix.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None or not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expires_at(cached_at: datetime | None, max_age_days: int) -> datetime | None:
    origin = utc_timestamp(cached_at)
    if origin is None:
        return None
    if not isinstance(max_age_days, int) or isinstance(max_age_days, bool) or not 1 <= max_age_days <= 30:
        raise ValueError("Video cache retention must be 1..30 days")
    return origin + timedelta(days=max_age_days)


def is_expired(cached_at: datetime | None, max_age_days: int, *, now: datetime | None = None) -> bool:
    deadline = expires_at(cached_at, max_age_days)
    # Without a persisted cache timestamp there is no verifiable lifetime.
    if deadline is None:
        return True
    current = utc_timestamp(now) if now is not None else datetime.now(timezone.utc)
    return current is None or deadline <= current
