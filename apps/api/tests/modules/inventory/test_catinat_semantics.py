from datetime import date
from decimal import Decimal

from app.modules.inventory.daily_sheet.catinat_semantics import (
    parse_stock_containers, parse_usage,
)
from app.modules.inventory.daily_sheet.catinat_closing import plan_catinat_missing_closings

DAY = date(2026, 10, 19)


def test_two_shift_usage_has_distinct_morning_and_evening():
    usage = parse_usage("8+4")
    assert usage.status == "two_shifts"
    assert (usage.morning, usage.evening, usage.total) == (
        Decimal(8), Decimal(4), Decimal(12),
    )


def test_seeded_and_seedless_lemon_are_not_implicitly_two_shifts():
    for raw in ("4 hạt + 2 ko hạt", "4 hạt + 2 không hạt"):
        usage = parse_usage(raw)
        assert usage.status == "variant_not_shifts"
        assert usage.total is None
        assert usage.variants == (("seeded", Decimal(4)), ("seedless", Decimal(2)))


def test_usage_formats_without_shift_identity_are_queued():
    assert parse_usage("3miếng").status == "needs_review"
    assert parse_usage("14/10(657,4+730,2gr)").status == "needs_review"


def test_carrots_plus_means_containers_not_shifts():
    state = parse_stock_containers("22+39miếng")
    assert state.quantities == (Decimal(22), Decimal(39))
    assert state.unit == "miếng"
    assert state.status == "quantities_unverified_container_identity"


def test_typo_of_piece_unit_retains_source_raw():
    state = parse_stock_containers("19+39 miếnh")
    assert state.raw == "19+39 miếnh"
    assert state.unit == "miếng"
    assert state.quantities == (Decimal(19), Decimal(39))


def test_date_prefixed_batch_is_not_timestamp_verified():
    state = parse_stock_containers("14/10(657,4+730,2gr)")
    assert state.production_date_text == "14/10"
    assert state.quantities == (Decimal("657.4"), Decimal("730.2"))
    assert state.unit == "gr"
    assert state.prepared_at_verified is False


def test_invalid_or_incomplete_multi_container_requires_review():
    assert parse_stock_containers("14/10(657,4+730,2gr").status == "needs_review"


def _rows(usage):
    rows = [[] for _ in range(12)]
    rows[2] = [None, "19/10/2026"]
    rows[4] = [
        "STT", "Tên Nguyên Liệu / Vật Tư", "", "SL Đầu Ca / Nhận",
        "SL Sử Dụng Pha Chế", "", "Nhập Hàng", "SL Huỷ / Hư Hỏng",
        "", "", "Tồn Cuối Ca",
    ]
    rows[10] = [3, "Tắc", None, "10", usage, "", "30", "0", "", "", ""]
    return rows


def test_closing_preview_subtracts_both_shifts_but_is_not_verified_physical():
    output = plan_catinat_missing_closings(_rows("8+4"), business_date=DAY)
    assert len(output.suggestions) == 1
    assert output.suggestions[0].computed_value == "28"
    assert output.suggestions[0].status == "computed_unverified"


def test_variant_plus_cannot_be_used_as_shift_total():
    output = plan_catinat_missing_closings(_rows("4hạt+2ko hạt"), business_date=DAY)
    assert not output.suggestions
    assert output.blocked[0].reason == "missing_or_ambiguous_numeric_evidence"
