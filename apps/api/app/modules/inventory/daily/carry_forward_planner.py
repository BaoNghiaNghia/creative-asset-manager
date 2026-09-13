"""Read-only Gemini tool host for shared-workbook carry-forward planning.

The model is deliberately never given a spreadsheet id and this module exposes
no write operation.  The trusted service validates and writes an accepted plan.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.modules.inventory.ai.gateway import InventoryAiGatewayError, RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.daily.carry_forward import CarryForwardPlan, CarryForwardReviewRequired
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryItemModel, InventoryLocationModel
from app.providers.google.auth import get_connection_access_token


def _hash(sheet: str, cell: str, value: Any) -> str:
    # The writer re-reads and validates this same raw-value digest immediately
    # before external writes. Workbook identity is bound by the tool role.
    return canonical_hash([value])


class CarryForwardToolHost:
    def __init__(self, *, tenant_id: str, source_id: str, target_id: str, google: Any, sessions: sessionmaker[Session]):
        self.tenant_id, self.source_id, self.target_id, self.google, self.sessions = tenant_id, source_id, target_id, google, sessions
        self.ledger: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.plan: CarryForwardPlan | None = None
        self.submitted = False

    def _metadata(self, role: str) -> dict[str, Any]:
        file_id = self.source_id if role == "previous_gemini" else self.target_id if role == "shared_current" else None
        if not file_id: raise CarryForwardReviewRequired("invalid_workbook_role")
        metadata = self.google.spreadsheet_metadata(file_id)
        return {"role": role, "sheets": [{"sheetId": (s.get("properties") or {}).get("sheetId"), "title": (s.get("properties") or {}).get("title"), "index": i, "grid": (s.get("properties") or {}).get("gridProperties") or {}, "merged_ranges": s.get("merges") or [], "protected_ranges": s.get("protectedRanges") or []} for i, s in enumerate(metadata.get("sheets") or [])]}

    def _read(self, role: str, args: Mapping[str, Any]) -> dict[str, Any]:
        file_id = self.source_id if role == "previous_gemini" else self.target_id if role == "shared_current" else None
        if not file_id: raise CarryForwardReviewRequired("invalid_workbook_role")
        sheet, cells = str(args.get("sheet") or ""), list(args.get("cells") or [])
        if not sheet or not cells: raise CarryForwardReviewRequired("cells_required")
        evidence = []
        for cell in cells:
            cell = str(cell).upper()
            value = self.google.batch_get_values(file_id, [f"'{sheet}'!{cell}"], value_render_option="UNFORMATTED_VALUE")
            values = (value[0].get("values") or [[]])[0] if value else []
            raw = values[0] if values else None
            item = {"sheet": sheet, "cell": cell, "raw_value": raw, "evidence_hash": _hash(sheet, cell, raw)}
            self.ledger[(role, sheet, cell)] = item
            evidence.append(item)
        return {"role": role, "cells": evidence}

    def _read_range(self, role: str, args: Mapping[str, Any]) -> dict[str, Any]:
        sheet, a1_range = str(args.get("sheet") or ""), str(args.get("a1_range") or "")
        if not sheet or not a1_range or len(a1_range) > 64:
            raise CarryForwardReviewRequired("range_required")
        result = self.google.batch_get_values(
            self.source_id if role == "previous_gemini" else self.target_id,
            [f"'{sheet}'!{a1_range}"], value_render_option="UNFORMATTED_VALUE",
        )
        # Range reads are evidence only when the returned address can be mapped
        # unambiguously.  Reuse the cell reader for every bounded cell ledger.
        values = (result[0].get("values") or []) if result else []
        import re
        match = re.fullmatch(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)", a1_range)
        if not match or len(values) > 200:
            raise CarryForwardReviewRequired("invalid_or_oversized_range")
        def col(value: str) -> int:
            total = 0
            for char in value.upper(): total = total * 26 + ord(char) - ord("A") + 1
            return total
        start_col, start_row, end_col, end_row = col(match.group(1)), int(match.group(2)), col(match.group(3)), int(match.group(4))
        if end_col < start_col or end_row < start_row or (end_col - start_col + 1) * (end_row - start_row + 1) > 500:
            raise CarryForwardReviewRequired("invalid_or_oversized_range")
        def cell(column: int, row: int) -> str:
            letters = ""
            while column:
                column, remainder = divmod(column - 1, 26); letters = chr(65 + remainder) + letters
            return f"{letters}{row}"
        evidence = []
        for row_index in range(start_row, end_row + 1):
            for column_index in range(start_col, end_col + 1):
                raw = values[row_index - start_row][column_index - start_col] if row_index - start_row < len(values) and column_index - start_col < len(values[row_index - start_row]) else None
                item = {"sheet": sheet, "cell": cell(column_index, row_index), "raw_value": raw, "evidence_hash": _hash(sheet, cell(column_index, row_index), raw)}
                self.ledger[(role, sheet, item["cell"])] = item; evidence.append(item)
        return {"role": role, "cells": evidence}

    def _number(self, value: Any) -> Decimal:
        if value is None or (isinstance(value, str) and not value.strip()): raise CarryForwardReviewRequired("missing_closing_evidence")
        try: return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError) as exc: raise CarryForwardReviewRequired("non_numeric_closing_evidence") from exc

    def _catalog(self, warehouse: bool) -> dict[str, Any]:
        with self.sessions() as session:
            if warehouse:
                rows = session.scalars(select(InventoryLocationModel).where(InventoryLocationModel.tenant_id == self.tenant_id, InventoryLocationModel.active.is_(True))).all()
                return {"warehouses": [{"warehouse_id": r.id, "code": r.code, "name": r.name} for r in rows]}
            rows = session.scalars(select(InventoryItemModel).where(InventoryItemModel.tenant_id == self.tenant_id, InventoryItemModel.active.is_(True))).all()
            return {"materials": [{"material_id": r.id, "sku": r.sku, "name": r.name, "category": r.category, "base_unit": r.base_unit, "preferred_unit": r.preferred_unit} for r in rows]}

    def submit_carry_forward_plan(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if self.submitted: raise CarryForwardReviewRequired("carry_forward_plan_already_submitted")
        self.submitted = True
        rows, issues = list(args.get("operations") or args.get("rows") or []), list(args.get("issues") or [])
        targets: set[tuple[str, str]] = set()
        accepted = []
        for row in rows:
            operation_type = str(row.get("type") or "set_cell")
            source, target = dict(row.get("source") or {}), dict(row.get("target") or {})
            semantic_context = dict(row.get("semantic_context") or {})
            source_key = ("previous_gemini", str(source.get("sheet") or ""), str(source.get("cell") or "").upper())
            target_key = ("shared_current", str(target.get("sheet") or ""), str(target.get("cell") or "").upper())
            source_evidence, target_evidence = self.ledger.get(source_key), self.ledger.get(target_key)
            if not target_evidence or (operation_type == "set_cell" and not source_evidence): raise CarryForwardReviewRequired("target_or_source_not_read")
            if target.get("evidence_hash") != target_evidence["evidence_hash"] or (operation_type == "set_cell" and source.get("evidence_hash") != source_evidence["evidence_hash"]): raise CarryForwardReviewRequired("fabricated_evidence_hash")
            if operation_type == "clear_cell":
                if target_key[1:] in targets: raise CarryForwardReviewRequired("duplicate_or_conflicting_target")
                targets.add(target_key[1:])
                accepted.append({"type": "clear_cell", "semantic_context": semantic_context, "target": {**target, "spreadsheet_file_id": self.target_id}})
                continue
            if operation_type != "set_cell": raise CarryForwardReviewRequired("unsupported_carry_forward_operation")
            source_value = self._number(source_evidence["raw_value"])
            if self._number(source.get("closing_value")) != source_value or self._number(target.get("opening_value")) != source_value: raise CarryForwardReviewRequired("closing_opening_mismatch")
            if target_key[1:] in targets: raise CarryForwardReviewRequired("duplicate_or_conflicting_target")
            targets.add(target_key[1:])
            with self.sessions() as session:
                material_ok = session.scalar(select(InventoryItemModel.id).where(InventoryItemModel.id == row.get("material_id"), InventoryItemModel.tenant_id == self.tenant_id, InventoryItemModel.active.is_(True)))
                warehouse_ok = session.scalar(select(InventoryLocationModel.id).where(InventoryLocationModel.id == row.get("warehouse_id"), InventoryLocationModel.tenant_id == self.tenant_id, InventoryLocationModel.active.is_(True)))
            if not material_ok: raise CarryForwardReviewRequired("unknown_material")
            if not warehouse_ok: raise CarryForwardReviewRequired("unknown_warehouse")
            accepted.append({"type": "set_cell", "material_id": row.get("material_id"), "warehouse_id": row.get("warehouse_id"), "semantic_context": semantic_context, "value": row.get("value", target.get("opening_value")), "source": {**source, "spreadsheet_file_id": self.source_id}, "target": {**target, "spreadsheet_file_id": self.target_id}})
        self.plan = CarryForwardPlan(accepted, issues, None, 3)
        return {"accepted": True, "operations": len(accepted), "issues": len(issues)}

    def execute(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {"get_source_workbook_metadata": lambda _: self._metadata("previous_gemini"), "get_target_workbook_metadata": lambda _: self._metadata("shared_current"), "read_source_cells": lambda a: self._read("previous_gemini", a), "read_target_cells": lambda a: self._read("shared_current", a), "read_source_range": lambda a: self._read_range("previous_gemini", a), "read_target_range": lambda a: self._read_range("shared_current", a), "get_material_catalog": lambda _: self._catalog(False), "get_warehouse_catalog": lambda _: self._catalog(True), "submit_carry_forward_plan": self.submit_carry_forward_plan}
        if name not in handlers: raise CarryForwardReviewRequired("unknown_carry_forward_tool")
        return handlers[name](args)


def function_declarations() -> list[dict[str, Any]]:
    cells = {"type": "object", "properties": {"sheet": {"type": "string"}, "cells": {"type": "array", "items": {"type": "string"}}}, "required": ["sheet", "cells"]}
    range_read = {"type": "object", "properties": {"sheet": {"type": "string"}, "a1_range": {"type": "string"}}, "required": ["sheet", "a1_range"]}
    return [{"name": n, "parameters": cells if "cells" in n else range_read if "range" in n else {"type": "object", "properties": {}}} for n in ("get_source_workbook_metadata", "get_target_workbook_metadata", "read_source_cells", "read_target_cells", "read_source_range", "read_target_range", "get_material_catalog", "get_warehouse_catalog")] + [{"name": "submit_carry_forward_plan", "parameters": {"type": "object", "properties": {"operations": {"type": "array"}, "issues": {"type": "array"}}, "required": ["operations", "issues"]}}]


class GeminiCarryForwardPlanner:
    def __init__(self, sessions: sessionmaker[Session], gateway: RuntimeInventoryGeminiGateway, *, enabled: bool, client_factory=GoogleSheetsInventoryClient, token_resolver=get_connection_access_token):
        self.sessions, self.gateway, self.enabled, self.client_factory, self.token_resolver = sessions, gateway, enabled, client_factory, token_resolver
    def _runtime(self, tenant_id: str) -> tuple[str, tuple[str, ...]]:
        if not self.enabled: raise CarryForwardReviewRequired("inventory_ai_disabled")
        with self.sessions() as session: control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
        if control is None or not control.enabled: raise CarryForwardReviewRequired("inventory_ai_disabled")
        if control.emergency_stop: raise CarryForwardReviewRequired("inventory_ai_emergency_stop")
        models = tuple(str(v) for v in (control.allowed_models_json or []) if v)
        if control.provider != "gemini" or not models: raise CarryForwardReviewRequired("inventory_ai_model_not_allowed")
        return control.provider, models
    def plan(self, *, tenant_id: str, previous_gemini_file_id: str, shared_workbook_id: str, prompt: str, connection_id: str = "") -> CarryForwardPlan:
        provider, models = self._runtime(tenant_id)
        if not connection_id: raise CarryForwardReviewRequired("carry_forward_google_connection_missing")
        value = self.token_resolver(connection_id)
        access_token = asyncio.run(value) if hasattr(value, "__await__") else str(value)
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt + "\nThe server has bound roles previous_gemini and shared_current. Use the read-only tools. Call submit_carry_forward_plan exactly once."}]}]
        with self.client_factory(access_token) as google:
            host = CarryForwardToolHost(tenant_id=tenant_id, source_id=previous_gemini_file_id, target_id=shared_workbook_id, google=google, sessions=self.sessions)
            for model in models:
                try:
                    for _round in range(12):
                        turn = self.gateway.generate_tool_turn(tenant_id=tenant_id, contents=contents, function_declarations=function_declarations(), provider=provider, model=model)
                        contents.append(dict(turn.content))
                        if not turn.calls: break
                        responses = []
                        for call in turn.calls:
                            result = host.execute(call.name, call.arguments)
                            responses.append({"functionResponse": {"name": call.name, "response": result}})
                        contents.append({"role": "user", "parts": responses})
                        if host.plan is not None: return host.plan
                except InventoryAiGatewayError as exc:
                    if exc.code == "inventory_gemini_rate_limited": continue
                    raise CarryForwardReviewRequired(exc.code) from exc
        raise CarryForwardReviewRequired("carry_forward_plan_not_submitted")


def build_carry_forward_planner(*, session_factory: sessionmaker[Session], settings: Settings | None = None, client_factory=GoogleSheetsInventoryClient, token_resolver=get_connection_access_token) -> GeminiCarryForwardPlanner:
    runtime = settings or get_settings()
    return GeminiCarryForwardPlanner(session_factory, RuntimeInventoryGeminiGateway(InventoryGeminiCredentialResolver(session_factory, runtime), timeout_seconds=runtime.INVENTORY_AI_TIMEOUT_SECONDS), enabled=runtime.INVENTORY_AI_ENABLED, client_factory=client_factory, token_resolver=token_resolver)
