from __future__ import annotations
import json
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryItemModel
from .contracts import EditPlan, PlanSource, WorkbookSnapshot
PLANNER_PROMPT_VERSION = "inventory-sheet-agent-v3-2"
PLANNER_INSTRUCTIONS = """You are the Inventory Google Sheet editor/planner.
Infer workbook semantics only from the immutable snapshot, explicit cell-addressed evidence, and approved material catalog.
Return edits, not prose, strictly according to the JSON schema.
Python owns the security/source binding: spreadsheet ID, source hash, sheet, and authorized requested range. Copy that binding exactly; never shrink or expand it based on the observed used extent.
Preserve workbook identity, labels, formatting, formulas, headers, and structure.
Every issue.cells, operation.cell, operation.evidence_cells, and operation.copy_from A1 address MUST be copied from the supplied cell_evidence. Never calculate an A1 address from an array position.
Use exact source evidence and include evidence_cells; use copy_from for faithful copies.
Never invent missing quantities. Blank is not zero. If final/closing evidence needed for carry-forward is blank, do not fabricate an opening value and do not clear unresolved source evidence.
Malformed or split cells require review. A proposed repair must cite the exact supporting cell_evidence addresses.
For material_actions.source_key, preserve the exact workbook material/item identity value from the row when one exists; do not substitute the canonical material name. Infer the identity field from workbook semantics, not hard-coded columns.
If an identifiable workbook material has no approved catalog match, emit NEW_MATERIAL, POSSIBLE_RENAME, or AMBIGUOUS as appropriate and require review. Do not emit a material action when corruption prevents safe identification.
A repair may be proposed only when strongly supported and must require review.
Never silently repair corrupted structure.
New materials, possible renames, ambiguous materials, or unit changes require review.
Do not perform external actions and never claim edits were executed.
Do not return credentials, executable code, or Google API requests."""


def build_cell_evidence(snapshot: WorkbookSnapshot) -> list[dict[str, Any]]:
    last_row = last_column = -1
    for row_index, coordinates in enumerate(snapshot.coordinates):
        for column_index, _coordinate in enumerate(coordinates):
            raw_value = (
                snapshot.raw_values[row_index][column_index]
                if row_index < len(snapshot.raw_values)
                and column_index < len(snapshot.raw_values[row_index])
                else None
            )
            formula_value = (
                snapshot.formulas[row_index][column_index]
                if row_index < len(snapshot.formulas)
                and column_index < len(snapshot.formulas[row_index])
                else None
            )
            if raw_value not in (None, "") or (
                isinstance(formula_value, str) and formula_value.startswith("=")
            ):
                last_row = max(last_row, row_index)
                last_column = max(last_column, column_index)

    cells: list[dict[str, Any]] = []
    if last_row < 0 or last_column < 0:
        return cells
    for row_index, coordinates in enumerate(snapshot.coordinates[: last_row + 1]):
        for column_index, coordinate in enumerate(coordinates[: last_column + 1]):
            raw_value = (
                snapshot.raw_values[row_index][column_index]
                if row_index < len(snapshot.raw_values)
                and column_index < len(snapshot.raw_values[row_index])
                else None
            )
            formula_value = (
                snapshot.formulas[row_index][column_index]
                if row_index < len(snapshot.formulas)
                and column_index < len(snapshot.formulas[row_index])
                else None
            )
            formula = (
                formula_value
                if isinstance(formula_value, str) and formula_value.startswith("=")
                else None
            )
            cells.append({"cell": coordinate, "value": raw_value, "formula": formula})
    return cells


def bind_authoritative_source(plan: EditPlan, snapshot: WorkbookSnapshot) -> EditPlan:
    return plan.model_copy(
        update={
            "source": PlanSource(
                spreadsheet_file_id=snapshot.spreadsheet_file_id,
                source_hash=snapshot.source_hash,
                sheet=snapshot.sheet_title,
                range=snapshot.requested_range,
            )
        },
        deep=True,
    )

class SheetAgentUnavailable(RuntimeError): pass
class GeminiSheetAgentPlanner:
    def __init__(self, session_factory: sessionmaker[Session], gateway: RuntimeInventoryGeminiGateway, *, enabled: bool) -> None:
        self.session_factory, self.gateway, self.enabled = session_factory, gateway, enabled
    def _runtime(self, tenant_id: str) -> tuple[str, str]:
        if not self.enabled: raise SheetAgentUnavailable("inventory_ai_disabled")
        with self.session_factory() as session: control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
        if control is None or not control.enabled: raise SheetAgentUnavailable("inventory_ai_disabled")
        if control.emergency_stop: raise SheetAgentUnavailable("inventory_ai_emergency_stop")
        models = tuple(str(value) for value in (control.allowed_models_json or ()) if value)
        if control.provider != "gemini" or not models: raise SheetAgentUnavailable("inventory_ai_model_not_allowed")
        return control.provider, models[0]
    def _material_catalog(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(InventoryItemModel).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.active.is_(True)).order_by(InventoryItemModel.id)))
            return [{"material_id": row.id, "sku": row.sku, "canonical_name": row.name, "category": row.category, "preferred_unit": row.preferred_unit} for row in rows]
    def plan(self, *, tenant_id: str, business_date: str, snapshot: WorkbookSnapshot, business_goal: list[str]) -> tuple[EditPlan, str]:
        provider, model = self._runtime(tenant_id)
        snapshot_payload = snapshot.model_dump(mode="json")
        for matrix_field in ("raw_values", "formulas", "coordinates"):
            snapshot_payload.pop(matrix_field, None)
        payload = {
            "prompt_version": PLANNER_PROMPT_VERSION,
            "business_date": business_date,
            "business_goal": business_goal,
            "snapshot": snapshot_payload,
            "cell_evidence": build_cell_evidence(snapshot),
            "approved_material_catalog": self._material_catalog(tenant_id),
        }
        prompt = PLANNER_INSTRUCTIONS + "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        result = self.gateway.analyze_structured_text(tenant_id=tenant_id, prompt=prompt, schema=EditPlan.model_json_schema(), provider=provider, model=model)
        proposal = EditPlan.model_validate(dict(result.extracted_json))
        return bind_authoritative_source(proposal, snapshot), model
