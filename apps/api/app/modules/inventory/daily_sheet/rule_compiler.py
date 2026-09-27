from __future__ import annotations

from typing import Any

_ALLOWED_OPERATIONS = frozenset({"exact_copy", "set_cell"})


def compile_active_rules(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Prove complete LOW-risk mechanical contracts; never read/write Sheets."""
    compiled: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for entry in entries:
        key = str(entry.get("knowledge_key") or "")
        rule = dict(entry.get("structured_rule") or {})
        if rule.get("suspended") or str(rule.get("state") or "").lower() == "suspended" or str(rule.get("mode") or "").lower() == "suspended":
            rejected.append({"knowledge_key": key, "reason": "suspended"}); continue
        if rule.get("executable") is not True:
            rejected.append({"knowledge_key": key, "reason": "not_executable"}); continue
        if str(rule.get("risk_level") or "").upper() != "LOW":
            rejected.append({"knowledge_key": key, "reason": "risk_not_low"}); continue
        operation = str(rule.get("operation") or rule.get("operation_type") or "").lower()
        if operation not in _ALLOWED_OPERATIONS:
            rejected.append({"knowledge_key": key, "reason": "operation_not_allowed"}); continue
        target = dict(rule.get("target") or {})
        target_sheet = str(target.get("sheet") or rule.get("target_sheet") or "").strip()
        target_cell = str(target.get("cell") or rule.get("target_cell") or "").strip()
        if not target_sheet or not target_cell or ":" in target_cell:
            rejected.append({"knowledge_key": key, "reason": "invalid_target"}); continue
        item: dict[str, Any] = {"knowledge_key": key, "operation": operation, "target_sheet": target_sheet, "target_cell": target_cell, "risk_level": "LOW"}
        if operation == "exact_copy":
            source = dict(rule.get("source") or {})
            source_sheet = str(source.get("sheet") or rule.get("source_sheet") or "").strip()
            source_cell = str(source.get("cell") or rule.get("source_cell") or "").strip()
            if not source_sheet or not source_cell or ":" in source_cell:
                rejected.append({"knowledge_key": key, "reason": "invalid_source"}); continue
            item.update({"source_sheet": source_sheet, "source_cell": source_cell})
        else:
            if "value" not in rule:
                rejected.append({"knowledge_key": key, "reason": "missing_literal_value"}); continue
            item["value"] = rule["value"]
        compiled.append(item)
    return {"compiled": compiled, "rejected": rejected, "count": len(compiled)}
