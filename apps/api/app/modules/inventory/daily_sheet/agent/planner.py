from __future__ import annotations
import json
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryItemModel
from .contracts import EditPlan, WorkbookSnapshot
PLANNER_PROMPT_VERSION = "inventory-sheet-agent-v3-1"
PLANNER_INSTRUCTIONS = """You are the Inventory Google Sheet editor/planner.
Infer workbook semantics only from the immutable snapshot and approved material catalog.
Return edits, not prose, strictly according to the JSON schema.
Preserve workbook identity, labels, formatting, formulas, headers, and structure.
Never invent missing quantities. Blank is not zero.
Use source evidence and include evidence_cells; use copy_from for faithful copies.
A repair may be proposed only when strongly supported and must require review.
Never silently repair corrupted structure.
New materials, possible renames, ambiguous materials, or unit changes require review.
Do not perform external actions and never claim edits were executed.
Do not return credentials, executable code, or Google API requests."""
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
        payload = {"prompt_version": PLANNER_PROMPT_VERSION, "business_date": business_date, "business_goal": business_goal, "snapshot": snapshot.model_dump(mode="json"), "approved_material_catalog": self._material_catalog(tenant_id)}
        prompt = PLANNER_INSTRUCTIONS + "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        result = self.gateway.analyze_structured_text(tenant_id=tenant_id, prompt=prompt, schema=EditPlan.model_json_schema(), provider=provider, model=model)
        return EditPlan.model_validate(dict(result.extracted_json)), model
