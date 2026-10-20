from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.modules.inventory.daily.shelf_life import (
    ShelfLifeState,
    opened_product_expiry,
    prepared_expiry,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def dt(day, hour=15, minute=30):
    return datetime(2026, 10, day, hour, minute, tzinfo=VN)


def test_prepared_exactly_96_hours_not_calendar_end():
    before = prepared_expiry(dt(20), as_of=dt(24, 15, 29))
    at = prepared_expiry(dt(20), as_of=dt(24))
    assert before.usable is True
    assert at.usable is False
    assert at.expires_at == dt(24)
    assert at.reason == "expired"


def test_prepared_shorter_policy_wins():
    decision = prepared_expiry(dt(20), shorter_safety_deadline=dt(21), as_of=dt(22))
    assert decision.expires_at == dt(21)
    assert not decision.usable


def test_missing_preparation_time_quarantines():
    decision = prepared_expiry(None, as_of=dt(20))
    assert decision.state is ShelfLifeState.MISSING_PREPARATION_TIME
    assert not decision.usable


def test_naive_timestamps_rejected():
    with pytest.raises(ValueError, match="timezone_aware"):
        prepared_expiry(datetime(2026, 10, 20), as_of=dt(20))


def test_offset_aware_input_uses_vietnam_business_timezone():
    prepared = dt(20).astimezone(timezone.utc)
    decision = prepared_expiry(prepared, as_of=dt(24))
    assert decision.expires_at == dt(24)
    assert not decision.usable


def test_opened_unknown_time_never_guessed():
    decision = opened_product_expiry(
        None, approved_open_duration=timedelta(hours=48),
        package_expiry=dt(29), as_of=dt(20),
    )
    assert decision.state is ShelfLifeState.UNKNOWN_OPENED_TIME
    assert not decision.usable


def test_opened_requires_approved_policy():
    decision = opened_product_expiry(dt(20), approved_open_duration=None, package_expiry=dt(29), as_of=dt(20))
    assert decision.state is ShelfLifeState.MISSING_PRODUCT_POLICY
    assert not decision.usable


def test_opened_uses_shortest_safe_expiry():
    decision = opened_product_expiry(dt(20), approved_open_duration=timedelta(hours=48), package_expiry=dt(21), as_of=dt(22))
    assert decision.expires_at == dt(21)
    assert not decision.usable
