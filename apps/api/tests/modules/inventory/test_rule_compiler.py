from app.modules.inventory.daily_sheet.rule_compiler import compile_active_rules


def _entry(rule):
    return {"knowledge_key": "rule-1", "structured_rule": rule}


def test_compiles_low_risk_exact_copy_contract():
    result = compile_active_rules([_entry({
        "executable": True, "risk_level": "LOW", "operation": "exact_copy",
        "source": {"sheet": "Raw", "cell": "B2"},
        "target": {"sheet": "Daily", "cell": "C2"},
    })])
    assert result["count"] == 1
    assert result["compiled"][0]["source_cell"] == "B2"
    assert result["compiled"][0]["target_cell"] == "C2"


def test_rejects_non_executable_historical_rule():
    result = compile_active_rules([_entry({
        "executable": False, "risk_level": "LOW", "operation_type": "set_cell",
        "target_sheet": "Daily", "target_cell": "C2", "value": 3,
    })])
    assert result["count"] == 0
    assert result["rejected"][0]["reason"] == "not_executable"


def test_rejects_high_risk_formula_and_bulk_targets():
    high = compile_active_rules([_entry({
        "executable": True, "risk_level": "HIGH", "operation": "set_cell",
        "target_sheet": "Daily", "target_cell": "C2", "value": 3,
    })])
    formula = compile_active_rules([_entry({
        "executable": True, "risk_level": "LOW", "operation": "formula",
        "target_sheet": "Daily", "target_cell": "C2", "value": "=A1",
    })])
    bulk = compile_active_rules([_entry({
        "executable": True, "risk_level": "LOW", "operation": "set_cell",
        "target_sheet": "Daily", "target_cell": "C2:C9", "value": 3,
    })])
    assert high["count"] == formula["count"] == bulk["count"] == 0


def test_rejects_suspended_rule_even_if_executable():
    result = compile_active_rules([_entry({
        "executable": True, "risk_level": "LOW", "operation": "set_cell",
        "target_sheet": "Daily", "target_cell": "C2", "value": 3,
        "state": "suspended",
    })])
    assert result["count"] == 0
    assert result["rejected"][0]["reason"] == "suspended"
