"""Fail-closed regression for read-only verified Closing -> Opening parsing."""
from decimal import Decimal

import pytest

from app.modules.inventory.daily.closing_evidence import parse_verified_closing_number
from app.modules.inventory.daily.carry_forward import CarryForwardReviewRequired, _number
from app.modules.inventory.daily.carry_forward_planner import CarryForwardToolHost


@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    ("0", "0"),
    (12, "12"),
    ("12.75", "12.75"),
    ("12,5", "12.5"),
    ("1378,4", "1378.4"),
    (Decimal("0.1"), "0.1"),
])
def test_verified_stock_values_are_exact(value, expected):
    assert parse_verified_closing_number(value) == Decimal(expected)
    assert _number(value) == Decimal(expected)
    assert CarryForwardToolHost._number(None, value) == Decimal(expected)


@pytest.mark.parametrize("value,reason", [
    (None, "missing_closing_evidence"),
    ("", "missing_closing_evidence"),
    ("  ", "missing_closing_evidence"),
    ("/", "non_numeric_closing_evidence"),
    ("1/2 chai", "non_numeric_closing_evidence"),
    ("1( gãy)", "non_numeric_closing_evidence"),
    ("17+4", "non_numeric_closing_evidence"),
    ("=2+2", "non_numeric_closing_evidence"),
    ("NaN", "non_numeric_closing_evidence"),
    (float("nan"), "non_numeric_closing_evidence"),
    ("Infinity", "non_numeric_closing_evidence"),
    (float("inf"), "non_numeric_closing_evidence"),
    (Decimal("-1"), "non_numeric_closing_evidence"),
    (-1, "non_numeric_closing_evidence"),
    (False, "non_numeric_closing_evidence"),
    ("1,234", "ambiguous_closing_number_format"),
    ("1,234.50", "non_numeric_closing_evidence"),
])
def test_invalid_stock_never_admitted_to_carry_forward(value, reason):
    with pytest.raises(ValueError, match=f"^{reason}$"):
        parse_verified_closing_number(value)
    with pytest.raises(CarryForwardReviewRequired, match=f"^{reason}$"):
        _number(value)
    with pytest.raises(CarryForwardReviewRequired, match=f"^{reason}$"):
        CarryForwardToolHost._number(None, value)
