from decimal import Decimal
import pytest
from app.modules.inventory.daily_sheet.config import DailyCountSheetConfig, parse_daily_sheet_config
from app.modules.inventory.daily_sheet.parser import DailyCountSheetValidationError, parse_daily_count_records, parse_inventory_quantity

HEADERS = ["STT", "Tên Nguyên Liệu / Vật Tư", "Phân Loại", "SL Đầu Ca / Nhận", "SL Sử Dụng Pha Chế", "Nhập Hàng", "SL Huỷ / Hư Hỏng", "Tồn Cuối Ca"]

def config() -> DailyCountSheetConfig:
    return DailyCountSheetConfig.model_validate({
        "version": 2, "mode": "daily_count_sheet",
        "source": {
            "sheet": "Bảng Kiểm Kê Nguyên Liệu Vật Tư", "range": "A1:H1000", "header_row": 1,
            "item_row": {"strategy": "numeric_key", "key_column": "STT"},
            "columns": dict(zip(("item_key", "name", "category", "opening", "used", "inbound", "waste", "closing"), HEADERS, strict=True)),
            "warehouse": "main",
        },
        "stock": {"authoritative_column": "closing"},
        "reset": {"mode": "restore_template", "ranges": ["'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2:H1000"]},
        "reconciliation": {"mode": "report_only"},
    })

@pytest.mark.parametrize(("raw", "value", "unit"), [
    ("14", Decimal("14"), "count"),
    ("303,2g", Decimal("303.2"), "g"),
    ("1(312g); 2(303,2g)", Decimal("615.2"), "g"),
    ("1(550ml)", Decimal("550"), "ml"),
    ("1(110ml) ; 2(600ml)", Decimal("710"), "ml"),
    ("1(1l8)", Decimal("1800"), "ml"),
    ("1(4l4)", Decimal("4400"), "ml"),
    ("1(1l8) ; 2(1l8) ; 3(1l6) ; 4(2l4)", Decimal("7600"), "ml"),
])
def test_quantity_grammar(raw, value, unit):
    quantity = parse_inventory_quantity(raw)
    assert quantity.canonical_value == value
    assert quantity.canonical_unit == unit

@pytest.mark.parametrize("raw", ["1(350", "5g)", "1(300g); 2(500ml)"])
def test_quantity_rejects_malformed_or_incompatible(raw):
    with pytest.raises(ValueError):
        parse_inventory_quantity(raw)

def test_real_workbook_classification_and_shifted_error():
    rows = [HEADERS, ["II", "SECTION", "", "", "", "", "", ""], ["", "TỔNG Topping", "", "", "", "", "", ""]]
    rows += [[key, f"Item {key}", "Topping", "14", "", "", "", "10"] for key in range(25, 40)]
    rows[5][1], rows[5][3], rows[5][4] = "Kem trứng", "1(350", "5g)"
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(config(), {"values": rows})
    assert any(error["code"] == "suspected_shifted_quantity" and error["cell"] == "D6" for error in captured.value.errors)

def test_real_workbook_includes_only_numeric_item_rows():
    rows = [HEADERS, ["III", "Section", "", "", "", "", "", ""], ["", "TỔNG", "", "", "", "", "", ""]]
    rows += [[key, f"Item {key}", "Topping", "", "", "", "", "14"] for key in range(25, 40)]
    records, warnings = parse_daily_count_records(config(), {"values": rows})
    assert [key[1] for key in records] == [str(key) for key in range(25, 40)]
    assert warnings == []

def test_config_dispatch_preserves_v2():
    assert parse_daily_sheet_config(config().model_dump()).version == 2


def test_dynamic_catalog_accepts_appended_inserted_moved_and_new_categories():
    rows = [
        HEADERS,
        ["I", "Old section", "", "", "", "", "", ""],
        [25, "Material 25", "Original category", "", "", "", "", "10"],
        [40, "New material 40", "Brand new category", "", "", "", "", "5"],
        ["II", "Moved section", "", "", "", "", "", ""],
        [27, "Material 27", "Renamed category", "", "", "", "", "8"],
        [41, "New material 41", "Another category", "", "", "", "", "2"],
        [26, "Material 26", "Original category", "", "", "", "", "7"],
        [42, "New material 42", "Another category", "", "", "", "", "3"],
    ]
    records, warnings = parse_daily_count_records(config(), {"values": rows})
    assert [key[1] for key in records] == ["25", "40", "27", "41", "26", "42"]
    assert records[("main", "40")].category == "Brand new category"
    assert records[("main", "27")].source_row == 6
    assert warnings == []


def test_existing_baseline_can_grow_beyond_fifteen_materials_without_code_change():
    rows = [HEADERS]
    rows += [[key, f"Item {key}", f"Category {key % 4}", "", "", "", "", str(key)] for key in range(25, 46)]
    records, _ = parse_daily_count_records(config(), {"values": rows})
    assert len(records) == 21
    assert ("main", "40") in records
    assert ("main", "45") in records


def test_new_material_policy_defaults_to_review_required_and_accepts_all_modes():
    assert config().new_material_policy == "review_required"
    for policy in ("review_required", "auto_register_high_confidence", "ignore", "block"):
        value = config().model_dump()
        value["new_material_policy"] = policy
        assert DailyCountSheetConfig.model_validate(value).new_material_policy == policy


def test_deterministic_quantity_does_not_invoke_semantic_fallback():
    calls = []
    rows = [HEADERS, [25, "Material", "Category", "", "", "", "", "14"]]
    records, _ = parse_daily_count_records(
        config(), {"values": rows},
        quantity_semantic_analyzer=lambda payload: calls.append(payload),
    )
    assert records[("main", "25")].quantity.canonical_value == Decimal("14")
    assert calls == []


def _confident_quantity(raw: str, value: str, unit: str):
    return {
        "status": "parsed",
        "raw": raw,
        "canonical_value": value,
        "canonical_unit": unit,
        "confidence": 1,
        "requires_review": False,
        "warnings": [],
    }


def test_blank_authoritative_quantity_never_calls_gemini_or_attaches_suggestion():
    calls = []
    rows = [HEADERS, [25, "Material", "Category", "", "", "", "", ""]]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(),
            {"values": rows},
            quantity_semantic_analyzer=lambda payload: calls.append(payload)
            or _confident_quantity("", "0", "count"),
        )

    error = captured.value.errors[0]
    assert error["code"] == "blank_authoritative_quantity"
    assert "semantic_suggestion" not in error
    assert calls == []


def test_unmatched_parenthesis_cannot_be_auto_accepted_at_full_confidence():
    calls = []
    rows = [HEADERS, [25, "Material", "Category", "", "", "", "", "5g)"]]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(),
            {"values": rows},
            quantity_semantic_analyzer=lambda payload: calls.append(payload)
            or _confident_quantity("5g)", "5", "g"),
        )

    assert calls
    assert captured.value.errors[0]["code"] == "unmatched_quantity_parenthesis"
    assert captured.value.errors[0]["semantic_suggestion"]["confidence"] == 1


def test_suspected_shifted_quantity_cannot_be_auto_accepted_at_full_confidence():
    calls = []
    rows = [
        HEADERS,
        [28, "Kem trứng", "Topping", "1(350", "5g)", "", "", "10"],
    ]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(),
            {"values": rows},
            quantity_semantic_analyzer=lambda payload: calls.append(payload)
            or _confident_quantity(str(payload["raw"]), "350.5", "g"),
        )

    codes = {error["code"] for error in captured.value.errors}
    assert "suspected_shifted_quantity" in codes
    assert "unmatched_quantity_parenthesis" in codes
    assert len(calls) == 2


def test_non_structural_unfamiliar_quantity_can_use_safe_semantic_fallback():
    calls = []
    rows = [
        HEADERS,
        [25, "Material", "Category", "", "", "", "", "approximately 700ml"],
    ]
    records, warnings = parse_daily_count_records(
        config(),
        {"values": rows},
        quantity_semantic_analyzer=lambda payload: calls.append(payload)
        or _confident_quantity("approximately 700ml", "700", "ml"),
    )

    quantity = records[("main", "25")].quantity
    assert quantity.canonical_value == Decimal("700")
    assert quantity.canonical_unit == "ml"
    assert quantity.parse_status == "gemini_validated"
    assert calls[0]["deterministic_error"] == "unsupported_quantity_unit"
    assert warnings[0]["code"] == "quantity_semantic_fallback"


def test_unknown_quantity_invokes_semantic_fallback_but_unknown_package_still_blocks():
    calls = []
    rows = [HEADERS, [25, "Material", "Category", "", "", "", "", "1 crate"]]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(), {"values": rows},
            quantity_semantic_analyzer=lambda payload: calls.append(payload) or {
                "status": "parsed", "raw": "1 crate", "canonical_value": "12",
                "canonical_unit": "count", "confidence": 1,
                "requires_review": False, "warnings": [],
            },
        )
    assert calls and calls[0]["cell"] == "H2"
    assert captured.value.errors[0]["code"] == "unknown_package_conversion"


def test_malformed_split_invokes_semantic_fallback_and_remains_review_required():
    calls = []
    rows = [HEADERS, [25, "Material", "Category", "1(350", "5g)", "", "", "10"]]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(), {"values": rows},
            quantity_semantic_analyzer=lambda payload: calls.append(payload) or {
                "status": "parsed", "raw": "1(350", "canonical_value": "350.5",
                "canonical_unit": "g", "confidence": 1,
                "requires_review": True, "warnings": ["split across cells"],
            },
        )
    assert calls and calls[0]["nearby_business_cells"][1]["raw"] == "5g)"
    assert captured.value.errors[0]["code"] == "suspected_shifted_quantity"
    assert captured.value.errors[0]["semantic_suggestion"]["requires_review"] is True


def test_invalid_or_unavailable_semantic_output_never_invents_quantity():
    rows = [HEADERS, [25, "Material", "Category", "", "", "", "", "not a quantity"]]
    for analyzer in (
        lambda _payload: None,
        lambda _payload: {"status": "parsed", "raw": "different"},
    ):
        with pytest.raises(DailyCountSheetValidationError):
            parse_daily_count_records(
                config(), {"values": rows}, quantity_semantic_analyzer=analyzer
            )


def test_schema_drift_can_be_proposed_for_read_only_analysis_without_mutating_config():
    drifted = list(HEADERS)
    drifted[-1] = "Closing balance"
    approved = config()
    calls = []
    records, warnings = parse_daily_count_records(
        approved,
        {"values": [drifted, [25, "Material", "Category", "", "", "", "", "10"]]},
        schema_semantic_analyzer=lambda payload: calls.append(payload) or {
            "status": "mapped",
            "mapping": {**approved.source.columns.model_dump(), "closing": "Closing balance"},
            "confidence": 0.99,
            "requires_review": True,
            "reset_relevant_changed": True,
            "changes": ["closing header renamed"],
        },
    )
    assert records[("main", "25")].quantity.canonical_value == Decimal("10")
    assert warnings[0]["code"] == "schema_mapping_proposed"
    assert warnings[0]["reset_relevant_changed"] is True
    assert approved.source.columns.closing == "Tồn Cuối Ca"
    assert calls


def test_reset_relevant_drift_is_forced_to_review_even_if_gemini_says_safe():
    approved = config()
    drifted = list(HEADERS)
    drifted[-1] = "Closing balance"
    _, warnings = parse_daily_count_records(
        approved,
        {"values": [drifted, [25, "Material", "Category", "", "", "", "", "10"]]},
        schema_semantic_analyzer=lambda _payload: {
            "status": "mapped",
            "mapping": {**approved.source.columns.model_dump(), "closing": "Closing balance"},
            "confidence": 0.99,
            "requires_review": False,
            "reset_relevant_changed": False,
            "changes": [],
        },
    )
    assert warnings[0]["requires_review"] is True
    assert warnings[0]["reset_relevant_changed"] is True


def test_major_schema_drift_is_rejected_even_with_semantic_analyzer():
    rows = [["Unknown", "Columns"], ["x", "y"]]
    with pytest.raises(DailyCountSheetValidationError) as captured:
        parse_daily_count_records(
            config(), {"values": rows},
            schema_semantic_analyzer=lambda _payload: {
                "status": "major_drift", "mapping": {}, "confidence": 0.4,
                "requires_review": True, "reset_relevant_changed": True,
                "changes": ["major layout change"],
            },
        )
    assert any(error["code"] == "schema_semantic_mapping_rejected" for error in captured.value.errors)


def test_inserted_extra_column_uses_semantic_mapping_and_marks_layout_drift():
    calls = []
    approved = config()
    headers = HEADERS[:3] + ["Notes"] + HEADERS[3:]
    row = [25, "Material", "Category", "note", "", "", "", "", "10"]
    records, warnings = parse_daily_count_records(
        approved,
        {"values": [headers, row]},
        schema_semantic_analyzer=lambda payload: calls.append(payload) or {
            "status": "mapped",
            "mapping": approved.source.columns.model_dump(),
            "confidence": 0.99,
            "requires_review": True,
            "reset_relevant_changed": True,
            "changes": ["notes column inserted before reset-relevant columns"],
        },
    )
    assert records[("main", "25")].quantity.canonical_value == Decimal("10")
    assert calls[0]["layout_drift"] is True
    assert warnings[0]["code"] == "schema_mapping_proposed"
    assert warnings[0]["reset_relevant_changed"] is True


def test_moved_closing_column_uses_semantic_mapping_without_mutating_config():
    approved = config()
    headers = HEADERS[:-1]
    headers.insert(3, HEADERS[-1])
    row = [25, "Material", "Category", "10", "", "", "", ""]
    records, warnings = parse_daily_count_records(
        approved,
        {"values": [headers, row]},
        schema_semantic_analyzer=lambda payload: {
            "status": "mapped",
            "mapping": approved.source.columns.model_dump(),
            "confidence": 0.99,
            "requires_review": True,
            "reset_relevant_changed": True,
            "changes": ["closing column moved"],
        },
    )
    assert records[("main", "25")].quantity.canonical_value == Decimal("10")
    assert warnings[0]["code"] == "schema_mapping_proposed"
    assert approved.source.columns.closing == "Tồn Cuối Ca"
