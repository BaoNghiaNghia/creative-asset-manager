"""Evidence-based P0 regression using minimal user-uploaded Excel cell samples."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.inventory.daily_sheet.observation import observe_quantity
from app.modules.inventory.daily_sheet.parser import (
    DailySheetValidationError,
    parse_inventory_quantity,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "inventory_catinat_2026_10_18_golden.json"


def cases():
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["slash_semantics"] == "UNCONFIRMED_NEVER_ZERO"
    assert len(value["source_sha256"]) == 64
    return value["cases"]


@pytest.mark.parametrize("item", cases(), ids=lambda item: f"{item['sheet']}!{item['cell']}")
def test_catinat_source_cell_observations_are_non_destructive(item):
    observation = observe_quantity(item["value"], number_format=item.get("number_format"))
    assert observation.raw == str(item["value"])
    assert observation.status == item["expected"]
    assert observation.requires_review == (item["expected"] != "PARSED")
    if "reason" in item:
        assert observation.reason == item["reason"]
    if "quantity" in item:
        assert observation.quantity == Decimal(item["quantity"])
        assert observation.unit == item["unit"]


def test_catinat_unknown_values_never_silently_become_zero():
    for raw in ("", None, "/", "1( gãy)", "17+4"):
        output = observe_quantity(raw)
        assert output.requires_review
        assert output.quantity is None


def test_catinat_legacy_quantity_grammar_does_not_misread_commas():
    # In the old V1 parser, 1,5 used to become 15. Only clear grouping
    # like 1,234.50 is accepted; mixed locale must be resolved upstream.
    from app.modules.inventory.daily_sheet.parser import parse_decimal
    assert parse_decimal("1,234.50") == Decimal("1234.50")
    with pytest.raises(DailySheetValidationError):
        parse_decimal("1,5")


def test_catinat_gr_literal_is_a_gram_alias_not_a_package_conversion():
    value = parse_inventory_quantity("1378,4gr")
    assert value.canonical_value == Decimal("1378.4")
    assert value.canonical_unit == "g"
    with pytest.raises(DailySheetValidationError):
        parse_inventory_quantity("1+1/2 can")


def test_date_formatted_excel_serial_is_not_treated_as_quantity():
    corrupted = observe_quantity(46054, number_format="d/m", expected_unit="chai")
    assert corrupted.requires_review
    assert corrupted.quantity is None
    assert corrupted.reason == "excel_date_formatted_quantity"


def test_unrelated_warehouses_are_not_aggregated_from_matching_names():
    # This test enforces the intended identity invariant even if a material
    # name appears on multiple source tabs.
    from app.modules.inventory.daily_sheet.parser import StockRecord, build_variances
    old = {("kho_pha_che", "SYRUP"): StockRecord("kho_pha_che", "SYRUP", Decimal("3"))}
    today = {("phong_pha_che", "SYRUP"): StockRecord("phong_pha_che", "SYRUP", Decimal("1"))}
    with pytest.raises(DailySheetValidationError, match="incomplete_stock_record_set"):
        build_variances(today, old)
