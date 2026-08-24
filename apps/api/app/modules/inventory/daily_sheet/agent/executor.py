from __future__ import annotations
import hashlib, json
from decimal import Decimal, InvalidOperation
from typing import Callable
from .contracts import EditPlan, ExecutionResult, GuardResult, WorkbookSnapshot
class StaleEditPlan(RuntimeError): code = "stale_edit_plan"
class EditPlanVerificationError(RuntimeError): code = "edit_plan_verification_failed"
def plan_hash(plan: EditPlan) -> str:
    return hashlib.sha256(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
def _equivalent(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is actual
    expected_text, actual_text = str(expected).strip(), str(actual).strip()
    if isinstance(expected, str) and isinstance(actual, str):
        return expected_text == actual_text
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    try:
        return Decimal(expected_text.replace(",", ".")) == Decimal(actual_text.replace(",", "."))
    except (InvalidOperation, ValueError):
        return expected_text == actual_text
def _read_values(google, spreadsheet_id: str, ranges: list[str]) -> dict[str, object]:
    if not ranges: return {}
    blocks = google.batch_get_values(spreadsheet_id, ranges); result = {}
    for requested, block in zip(ranges, blocks, strict=False):
        values = block.get("values") or []; result[requested] = values[0][0] if values and values[0] else None
    return result
class GoogleSheetEditPlanExecutor:
    def __init__(self, *, google, snapshot_loader: Callable[[], WorkbookSnapshot]) -> None: self.google, self.snapshot_loader = google, snapshot_loader
    def execute(self, *, plan: EditPlan, snapshot: WorkbookSnapshot, guard: GuardResult, expected_plan_hash: str, expected_source_hash: str) -> ExecutionResult:
        computed_plan_hash = plan_hash(plan)
        if computed_plan_hash != expected_plan_hash or snapshot.source_hash != expected_source_hash: raise StaleEditPlan("stale_edit_plan")
        current = self.snapshot_loader()
        if current.source_hash != expected_source_hash: raise StaleEditPlan("stale_edit_plan")
        if not guard.accepted or guard.requires_review: return ExecutionResult(status="blocked", source_hash=current.source_hash, plan_hash=computed_plan_hash, verification_status="not_executed")
        spreadsheet_id = snapshot.spreadsheet_file_id
        def target(operation): return f"'{operation.sheet.replace(chr(39), chr(39) * 2)}'!{operation.cell}"
        target_ranges = [target(operation) for operation in plan.operations if operation.type in {"set_cell", "clear_cell"}]; before_state = _read_values(self.google, spreadsheet_id, target_ranges)
        set_ranges = [target(operation) for operation in guard.set_operations]
        if guard.set_operations:
            self.google.batch_update_values(spreadsheet_id, [{"range": item, "majorDimension": "ROWS", "values": [[operation.value]]} for item, operation in zip(set_ranges, guard.set_operations, strict=True)])
            observed = _read_values(self.google, spreadsheet_id, set_ranges)
            if any(not _equivalent(operation.value, observed.get(item)) for item, operation in zip(set_ranges, guard.set_operations, strict=True)): raise EditPlanVerificationError("edit_plan_set_verification_failed")
        clear_ranges = [target(operation) for operation in guard.clear_operations]
        if clear_ranges:
            self.google.batch_clear_values(spreadsheet_id, clear_ranges); observed = _read_values(self.google, spreadsheet_id, clear_ranges)
            if any(value not in (None, "") for value in observed.values()): raise EditPlanVerificationError("edit_plan_clear_verification_failed")
        return ExecutionResult(status="completed", source_hash=current.source_hash, plan_hash=computed_plan_hash, set_count=len(guard.set_operations), clear_count=len(guard.clear_operations), verification_status="verified", before_state=before_state)
