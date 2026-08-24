from __future__ import annotations
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Callable
from .contracts import EditPlan, GuardResult, WorkbookSnapshot
from .snapshot import snapshot_cell
_CELL_RE = re.compile(r"^[A-Z]+[1-9][0-9]*$")
_RANGE_RE = re.compile(r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^!]+))!(?P<c1>[A-Z]+)(?P<r1>[1-9][0-9]*):(?P<c2>[A-Z]+)(?P<r2>[1-9][0-9]*)$")
def _column_number(value: str) -> int:
    result = 0
    for character in value: result = result * 26 + ord(character) - 64
    return result
def _equivalent(left, right) -> bool:
    if left is None or right is None:
        return left is right
    left_text, right_text = str(left).strip(), str(right).strip()
    # Preserve structured raw text exactly. Numeric coercion is permitted only
    # across JSON scalar types (for example, Gemini 14 versus Sheets "14").
    if isinstance(left, str) and isinstance(right, str):
        return left_text == right_text
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    try:
        return Decimal(left_text.replace(",", ".")) == Decimal(right_text.replace(",", "."))
    except (InvalidOperation, ValueError):
        return left_text == right_text
def _identity_matches(expected: str, actual) -> bool:
    if actual is None or isinstance(actual, bool):
        return False
    return expected == str(actual)


def _inside_grid_range(value: str, *, sheet_id: int | None, column: int, row: int) -> bool:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return False
    grid = payload.get("range", payload) if isinstance(payload, dict) else {}
    if not isinstance(grid, dict):
        return False
    expected_sheet = grid.get("sheetId")
    if expected_sheet is not None and sheet_id is not None and int(expected_sheet) != sheet_id:
        return False
    # Google GridRange is zero-based, start-inclusive, end-exclusive.
    zero_row, zero_column = row - 1, column - 1
    return (
        int(grid.get("startRowIndex", 0)) <= zero_row < int(grid.get("endRowIndex", 1048576))
        and int(grid.get("startColumnIndex", 0)) <= zero_column < int(grid.get("endColumnIndex", 18278))
    )
class SheetAgentSafetyGuard:
    def __init__(self, *, material_validator: Callable[[str, str], bool] | None = None) -> None: self.material_validator = material_validator
    def validate(self, *, tenant_id: str, plan: EditPlan, snapshot: WorkbookSnapshot, allowed_range: str, max_operations: int, allow_structure_changes: bool, allow_formula_changes: bool) -> GuardResult:
        errors, reviews = [], []
        if plan.source.spreadsheet_file_id != snapshot.spreadsheet_file_id: errors.append("spreadsheet_file_mismatch")
        if plan.source.source_hash != snapshot.source_hash: errors.append("source_hash_mismatch")
        if plan.source.sheet != snapshot.sheet_title or plan.source.range != snapshot.requested_range: errors.append("source_binding_mismatch")
        if len(plan.operations) > max_operations: errors.append("operation_limit_exceeded")
        bounds = _RANGE_RE.fullmatch(allowed_range)
        if bounds is None: errors.append("invalid_allowed_range"); minimum_column = maximum_column = minimum_row = maximum_row = 0; allowed_sheet = ""
        else:
            allowed_sheet = (bounds.group("quoted") or bounds.group("plain") or "").replace("''", "'"); minimum_column, maximum_column = _column_number(bounds.group("c1")), _column_number(bounds.group("c2")); minimum_row, maximum_row = int(bounds.group("r1")), int(bounds.group("r2"))
        targets = set()
        for operation in plan.operations:
            key = (operation.sheet, operation.cell)
            if key in targets: errors.append(f"conflicting_target:{operation.sheet}!{operation.cell}")
            targets.add(key)
            if operation.type not in {"set_cell", "clear_cell"}: errors.append(f"unsupported_structure_operation:{operation.operation_id}"); continue
            if operation.sheet != allowed_sheet or not _CELL_RE.fullmatch(operation.cell): errors.append(f"target_out_of_range:{operation.operation_id}"); continue
            cell_match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", operation.cell); assert cell_match is not None
            column, row = _column_number(cell_match.group(1)), int(cell_match.group(2))
            if not (minimum_column <= column <= maximum_column and minimum_row <= row <= maximum_row): errors.append(f"target_out_of_range:{operation.operation_id}"); continue
            if any(_inside_grid_range(value, sheet_id=snapshot.sheet_id, column=column, row=row) for value in snapshot.protected_ranges):
                errors.append(f"protected_cell:{operation.operation_id}")
            if any(_inside_grid_range(value, sheet_id=snapshot.sheet_id, column=column, row=row) for value in snapshot.merged_ranges):
                errors.append(f"merged_cell:{operation.operation_id}")
            try: formula = snapshot_cell(snapshot, operation.cell, formulas=True)
            except KeyError: formula = None
            if isinstance(formula, str) and formula.startswith("=") and not allow_formula_changes: errors.append(f"formula_change_blocked:{operation.operation_id}")
            if operation.business_action == "data_repair": reviews.append(f"data_repair:{operation.operation_id}")
            if operation.requires_review: reviews.append(f"operation_review:{operation.operation_id}")
            if operation.type == "set_cell":
                if operation.copy_from:
                    if operation.copy_from not in operation.evidence_cells: errors.append(f"copy_provenance_missing:{operation.operation_id}")
                    try: source_value = snapshot_cell(snapshot, operation.copy_from)
                    except KeyError: errors.append(f"copy_source_out_of_range:{operation.operation_id}")
                    else:
                        if not _equivalent(operation.value, source_value):
                            reviews.append(f"transformed_value:{operation.operation_id}")
                            if source_value in (None, "") and str(operation.value).strip() in {"0", "0.0"}: errors.append(f"blank_to_zero:{operation.operation_id}")
                else: reviews.append(f"unproven_set:{operation.operation_id}")
        for action in plan.material_actions:
            action_key = action.source_key or action.action.lower()
            if action.source_key_cell is None:
                if action.action == "MATCH_EXISTING": errors.append(f"material_source_key_cell_missing:{action_key}")
                else: reviews.append(f"material_source_key_cell_missing:{action_key}")
            else:
                try: grounded_key = snapshot_cell(snapshot, action.source_key_cell)
                except KeyError: errors.append(f"material_source_key_cell_invalid:{action_key}")
                else:
                    if not _identity_matches(action.source_key, grounded_key): errors.append(f"material_source_key_mismatch:{action_key}")
            if (action.source_name is None) != (action.source_name_cell is None):
                errors.append(f"material_source_name_incomplete:{action_key}")
            elif action.source_name_cell is not None:
                try: grounded_name = snapshot_cell(snapshot, action.source_name_cell)
                except KeyError: errors.append(f"material_source_name_cell_invalid:{action_key}")
                else:
                    if not _identity_matches(action.source_name, grounded_name): errors.append(f"material_source_name_mismatch:{action_key}")
            if action.action == "MATCH_EXISTING":
                if not action.material_id or self.material_validator is None or not self.material_validator(tenant_id, action.material_id): errors.append(f"invalid_material_match:{action.source_key}")
            else: reviews.append(f"material_{action.action.lower()}:{action.source_key}")
        if plan.status == "blocked": errors.append("planner_blocked")
        if plan.status == "review_required" or plan.requires_review: reviews.append("plan_review_required")
        return GuardResult(accepted=not errors, requires_review=bool(reviews), errors=sorted(set(errors)), review_reasons=sorted(set(reviews)), set_operations=[item for item in plan.operations if item.type == "set_cell"], clear_operations=[item for item in plan.operations if item.type == "clear_cell"])
