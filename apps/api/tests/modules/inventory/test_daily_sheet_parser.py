from decimal import Decimal

import pytest

from app.modules.inventory.daily_sheet.config import DailySheetConfig
from app.modules.inventory.daily_sheet.parser import (
    DailySheetValidationError,
    parse_decimal,
    build_variances,
    canonical_hash,
    parse_stock_records,
)


def config():
    return DailySheetConfig.model_validate({
        "version": 1,
        "source_ranges": [{
            "sheet": "Stock",
            "range": "A1:C20",
            "header_row": 1,
            "sku_column": "SKU",
            "quantity_column": "Quantity",
            "warehouse_column": "Warehouse",
        }],
        "reset": {"mode": "clear_ranges", "ranges": ["Stock!D2:D20"]},
        "targets": [{
            "warehouse": "Main Warehouse",
            "sheet": "Target",
            "sku_range": "Target!A2:A20",
            "quantity_column": "F",
        }],
        "new_sku_policy": "reject",
    })


def test_parser_normalizes_keys_and_uses_decimal():
    rows = [{"values": [
        [" SKU ", "Quantity", "Warehouse"],
        [" ab-1 ", "1,234.50", " Main  Warehouse "],
    ]}]
    result = parse_stock_records(config(), rows)
    record = result[("main_warehouse", "AB-1")]
    assert record.quantity == Decimal("1234.50")


@pytest.mark.parametrize("rows", [
    [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "1", "W"], ["A", "2", "W"]]}],
    [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "bad", "W"]]}],
    [{"values": [["SKU", "Quantity", "Warehouse"], ["", "1", "W"]]}],
])
def test_parser_blocks_duplicate_or_malformed_rows(rows):
    with pytest.raises(DailySheetValidationError):
        parse_stock_records(config(), rows)


def test_variance_and_hash_are_deterministic():
    previous = parse_stock_records(config(), [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "2", "Main Warehouse"]]}])
    current = parse_stock_records(config(), [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "5.25", "Main Warehouse"]]}])
    variance = build_variances(current, previous)
    assert variance[0]["variance"] == Decimal("3.25")
    assert canonical_hash(variance) == canonical_hash(list(variance))


def test_variance_refuses_missing_current_instead_of_inventing_zero():
    previous = parse_stock_records(config(), [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "2", "Main Warehouse"]]}])
    with pytest.raises(DailySheetValidationError, match="incomplete_stock_record_set"):
        build_variances({}, previous)


@pytest.mark.parametrize("value", ["1,5", "1,25", "1378,4", "1,23,4", "", "1/2 chai"])
def test_v1_parser_blocks_ambiguous_quantities(value):
    with pytest.raises(DailySheetValidationError, match="ambiguous_or_invalid_quantity_format"):
        parse_decimal(value)


def test_v1_keeps_explicit_thousands_grouping_and_dot_decimal():
    assert parse_decimal("1,234.50") == Decimal("1234.50")
    assert parse_decimal("1234.5") == Decimal("1234.5")


def test_v1_incomplete_snapshot_cannot_build_a_write_plan():
    current = [{"values": [["SKU", "Quantity", "Warehouse"], ["A", "1,5", "Main Warehouse"]]}]
    with pytest.raises(DailySheetValidationError, match="invalid quantity"):
        parse_stock_records(config(), current)


def test_config_allows_reset_inside_captured_source_range():
    value = config().model_dump()
    value["reset"]["ranges"] = ["Stock!C2:D4"]
    parsed = DailySheetConfig.model_validate(value)
    assert parsed.reset.ranges == ["Stock!C2:D4"]


def test_config_rejects_reset_overlap_with_target():
    value = config().model_dump()
    value["reset"]["ranges"] = ["Target!F2:F4"]
    with pytest.raises(ValueError, match="overlap"):
        DailySheetConfig.model_validate(value)
