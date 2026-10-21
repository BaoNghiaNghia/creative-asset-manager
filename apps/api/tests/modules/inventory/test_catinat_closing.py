from __future__ import annotations

from datetime import date

import pytest

from app.modules.inventory.daily_sheet.catinat_closing import (
    CatinatClosingLayoutError,
    plan_catinat_missing_closings,
)


DAY = date(2026, 10, 19)


def _grid():
    data = [[None] * 11 for _ in range(15)]
    data[2][1] = "19/10/2026"
    for col, label in {
        0: "STT",
        1: "Tên Nguyên Liệu / Vật Tư",
        3: "SL Đầu Ca / Nhận",
        4: "SL Sử Dụng Pha Chế",
        6: "Nhập Hàng",
        7: "SL Huỷ / Hư Hỏng",
        10: "Tồn Cuối Ca",
    }.items():
        data[4][col] = label
    data[10][0] = 3
    data[10][1] = "Tắc"
    data[10][3] = 10
    data[10][4] = 8
    data[10][6] = 30
    return data


def test_tac_closing_is_32_only_with_explicit_blank_movement_policy():
    data = _grid()
    disabled = plan_catinat_missing_closings(data, business_date=DAY)
    assert not disabled.suggestions
    assert [(b.row, b.reason) for b in disabled.blocked] == [
        (11, "missing_or_ambiguous_numeric_evidence")
    ]
    enabled = plan_catinat_missing_closings(
        data, business_date=DAY, assume_blank_movement_zero=True,
    )
    assert len(enabled.suggestions) == 1
    proposal = enabled.suggestions[0]
    assert proposal.target == "K11"
    assert proposal.computed_value == "32"
    assert proposal.inputs["E"] == "8"
    assert proposal.inputs["G"] == "30"
    assert proposal.status == "computed_unverified"
    assert len(proposal.evidence_hash) == 64


def test_does_not_overwrite_operator_closing_or_formula():
    for value in [32, "/", {"userEnteredValue": {"formulaValue": "=1+2"}}]:
        data = _grid()
        data[10][10] = value
        result = plan_catinat_missing_closings(
            data, business_date=DAY, assume_blank_movement_zero=True,
        )
        assert result.suggestions == ()


def test_slash_stock_means_zero_not_slash_inside_date():
    data = _grid()
    data[10][3] = "/"
    data[10][4] = 0
    data[10][6] = 5
    assert plan_catinat_missing_closings(
        data, business_date=DAY, assume_blank_movement_zero=True,
    ).suggestions[0].computed_value == "5"
    data[10][3] = "18/10(385gr)"
    result = plan_catinat_missing_closings(
        data, business_date=DAY, assume_blank_movement_zero=True,
    )
    assert not result.suggestions
    assert result.blocked[0].reason == "missing_or_ambiguous_numeric_evidence"


@pytest.mark.parametrize("opening,usage,incoming,waste,reason", [
    ("1+1/2can", 0, 0, 0, "missing_or_ambiguous_numeric_evidence"),
    ({"userEnteredValue": {"numberValue": 46054},
      "userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "d/m"}}},
     0, 0, 0, "missing_or_ambiguous_numeric_evidence"),
    (5, 8, 0, 0, "negative_expected_closing"),
    (5, "2 trái", 0, 0, "missing_or_ambiguous_numeric_evidence"),
    (5, "<200ml", 0, 0, "missing_or_ambiguous_numeric_evidence"),
    (5, 0, "1,234", 0, "missing_or_ambiguous_numeric_evidence"),
])
def test_ambiguous_and_negative_rows_blocked(opening, usage, incoming, waste, reason):
    data = _grid()
    data[10][3] = opening
    data[10][4] = usage
    data[10][6] = incoming
    data[10][7] = waste
    result = plan_catinat_missing_closings(
        data, business_date=DAY, assume_blank_movement_zero=True,
    )
    assert not result.suggestions
    assert result.blocked[0].reason == reason


def test_mixed_mass_uses_no_stock_count_formula():
    data = _grid()
    data[10][5] = 4
    result = plan_catinat_missing_closings(
        data, business_date=DAY, assume_blank_movement_zero=True,
    )
    assert not result.suggestions
    assert result.blocked[0].reason == "mixed_quantity_dimensions"


def test_business_date_and_layout_must_match():
    data = _grid()
    with pytest.raises(CatinatClosingLayoutError, match="report_business_date_mismatch"):
        plan_catinat_missing_closings(data, business_date=date(2026, 10, 20))
    data[4][10] = "Other column"
    with pytest.raises(CatinatClosingLayoutError, match="shift_report_headers_changed"):
        plan_catinat_missing_closings(data, business_date=DAY)


def test_does_not_modify_source_rows_or_numbers():
    data = _grid()
    before = [row.copy() for row in data]
    first = plan_catinat_missing_closings(data, business_date=DAY, assume_blank_movement_zero=True)
    data[10][4] = 9
    second = plan_catinat_missing_closings(data, business_date=DAY, assume_blank_movement_zero=True)
    assert first.suggestions[0].computed_value == "32"
    assert second.suggestions[0].computed_value == "31"
    assert first.suggestions[0].evidence_hash != second.suggestions[0].evidence_hash
    assert before[10][4] == 8
    assert data[10][10] is None
