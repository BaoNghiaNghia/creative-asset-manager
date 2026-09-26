"""Read-only Gemini tool host for shared-workbook carry-forward planning.

The model is deliberately never given a spreadsheet id and this module exposes
no write operation.  The trusted service validates and writes an accepted plan.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.modules.inventory.ai.gateway import InventoryAiGatewayError, RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.daily.carry_forward import CarryForwardPlan, CarryForwardReviewRequired
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryItemAliasModel, InventoryItemModel, InventoryLocationModel
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
        self.tool_trace: list[dict[str, Any]] = []

    def _metadata(self, role: str) -> dict[str, Any]:
        file_id = self.source_id if role == "previous_gemini" else self.target_id if role == "shared_current" else None
        if not file_id: raise CarryForwardReviewRequired("invalid_workbook_role")
        metadata = self.google.spreadsheet_metadata(file_id)
        return {"role": role, "sheets": [{"sheetId": (s.get("properties") or {}).get("sheetId"), "title": (s.get("properties") or {}).get("title"), "index": i, "grid": (s.get("properties") or {}).get("gridProperties") or {}, "merged_ranges": s.get("merges") or [], "protected_ranges": s.get("protectedRanges") or []} for i, s in enumerate(metadata.get("sheets") or [])]}

    def _read(self, role: str, args: Mapping[str, Any]) -> dict[str, Any]:
        file_id = self.source_id if role == "previous_gemini" else self.target_id if role == "shared_current" else None
        if not file_id:
            raise CarryForwardReviewRequired("invalid_workbook_role")
        sheet = str(args.get("sheet") or "")
        cells = [str(cell).upper() for cell in list(args.get("cells") or [])]
        if not sheet or not cells:
            raise CarryForwardReviewRequired("cells_required")
        if len(cells) > 100:
            raise CarryForwardReviewRequired("too_many_cells")
        ranges = [f"'{sheet}'!{cell}" for cell in cells]
        blocks = self.google.batch_get_values(
            file_id,
            ranges,
            value_render_option="UNFORMATTED_VALUE",
        )
        evidence = []
        for index, cell in enumerate(cells):
            values = (
                (blocks[index].get("values") or [[]])[0]
                if index < len(blocks)
                else []
            )
            raw = values[0] if values else None
            item = {
                "sheet": sheet,
                "cell": cell,
                "raw_value": raw,
                "evidence_hash": _hash(sheet, cell, raw),
            }
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

    def _catalog(self, warehouse: bool, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        query_text = str(args.get("query") or "").strip()
        category = str(args.get("category") or "").strip()
        try:
            requested_limit = int(args.get("limit") or 25)
        except (TypeError, ValueError):
            requested_limit = 25
        limit = min(max(requested_limit, 1), 50)
        needle = f"%{query_text.casefold()}%" if query_text else None
        with self.sessions() as session:
            if warehouse:
                query = select(InventoryLocationModel).where(
                    InventoryLocationModel.tenant_id == self.tenant_id,
                    InventoryLocationModel.active.is_(True),
                )
                if needle:
                    query = query.where(
                        or_(
                            func.lower(InventoryLocationModel.code).like(needle),
                            func.lower(InventoryLocationModel.name).like(needle),
                        )
                    )
                rows = list(
                    session.scalars(
                        query.order_by(InventoryLocationModel.name, InventoryLocationModel.id)
                        .limit(limit + 1)
                    )
                )
                truncated = len(rows) > limit
                rows = rows[:limit]
                return {
                    "warehouses": [
                        {"warehouse_id": r.id, "code": r.code, "name": r.name}
                        for r in rows
                    ],
                    "query": query_text or None,
                    "limit": limit,
                    "truncated": truncated,
                }
            query = select(InventoryItemModel).where(
                InventoryItemModel.tenant_id == self.tenant_id,
                InventoryItemModel.active.is_(True),
            )
            if category:
                query = query.where(
                    func.lower(func.coalesce(InventoryItemModel.category, ""))
                    == category.casefold()
                )
            if needle:
                alias_ids = select(InventoryItemAliasModel.item_id).where(
                    InventoryItemAliasModel.tenant_id == self.tenant_id,
                    func.lower(InventoryItemAliasModel.normalized_alias).like(needle),
                )
                query = query.where(
                    or_(
                        func.lower(InventoryItemModel.sku).like(needle),
                        func.lower(InventoryItemModel.name).like(needle),
                        func.lower(func.coalesce(InventoryItemModel.category, "")).like(needle),
                        InventoryItemModel.id.in_(alias_ids),
                    )
                )
            rows = list(
                session.scalars(
                    query.order_by(InventoryItemModel.name, InventoryItemModel.id)
                    .limit(limit + 1)
                )
            )
            truncated = len(rows) > limit
            rows = rows[:limit]
            return {
                "materials": [
                    {
                        "material_id": r.id,
                        "sku": r.sku,
                        "name": r.name,
                        "category": r.category,
                        "base_unit": r.base_unit,
                        "preferred_unit": r.preferred_unit,
                    }
                    for r in rows
                ],
                "query": query_text or None,
                "category": category or None,
                "limit": limit,
                "truncated": truncated,
            }

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
        handlers = {
            "get_source_workbook_metadata": lambda _: self._metadata("previous_gemini"),
            "get_target_workbook_metadata": lambda _: self._metadata("shared_current"),
            "read_source_cells": lambda a: self._read("previous_gemini", a),
            "read_target_cells": lambda a: self._read("shared_current", a),
            "read_source_range": lambda a: self._read_range("previous_gemini", a),
            "read_target_range": lambda a: self._read_range("shared_current", a),
            "get_material_catalog": lambda a: self._catalog(False, a),
            "get_warehouse_catalog": lambda a: self._catalog(True, a),
            "search_material_catalog": lambda a: self._catalog(False, a),
            "search_warehouse_catalog": lambda a: self._catalog(True, a),
            "submit_carry_forward_plan": self.submit_carry_forward_plan,
        }
        if name not in handlers:
            result = {
                "ok": False,
                "error": "unknown_carry_forward_tool",
                "allowed_tools": sorted(handlers),
            }
            self.tool_trace.append({"tool": name, "status": "rejected_unknown_tool"})
            return result
        result = handlers[name](args)
        trace: dict[str, Any] = {"tool": name}
        if name.startswith("get_source_"):
            trace["role"] = "previous_gemini"
        elif name.startswith("get_target_"):
            trace["role"] = "shared_current"
        if name in {"get_source_workbook_metadata", "get_target_workbook_metadata"}:
            trace["sheet_count"] = len(result.get("sheets") or [])
        elif name in {"read_source_cells", "read_target_cells"}:
            trace["sheet"] = str(args.get("sheet") or "")
            trace["cells"] = [str(value).upper() for value in list(args.get("cells") or [])[:100]]
            trace["cell_count"] = len(result.get("cells") or [])
        elif name in {"read_source_range", "read_target_range"}:
            trace["sheet"] = str(args.get("sheet") or "")
            trace["range"] = str(args.get("a1_range") or "")
            trace["cell_count"] = len(result.get("cells") or [])
        elif name in {"get_material_catalog", "search_material_catalog"}:
            trace["count"] = len(result.get("materials") or [])
        elif name in {"get_warehouse_catalog", "search_warehouse_catalog"}:
            trace["count"] = len(result.get("warehouses") or [])
        elif name == "submit_carry_forward_plan":
            trace["operation_count"] = int(result.get("operations") or 0)
            trace["issue_count"] = int(result.get("issues") or 0)
        self.tool_trace.append(trace)
        return result

    def audit_snapshot(self, *, model: str | None = None, rounds: int = 0) -> dict[str, Any]:
        return {
            "tool_rounds": rounds,
            "tool_trace": list(self.tool_trace),
            "read_ranges": [
                {
                    "role": item.get("role"),
                    "sheet": item.get("sheet"),
                    "range": item.get("range"),
                }
                for item in self.tool_trace
                if item.get("range")
            ],
            "evidence_cell_count": len(self.ledger),
            "operation_count": len(self.plan.rows) if self.plan is not None else 0,
            "issue_count": len(self.plan.issues) if self.plan is not None else 0,
            "model": model,
        }


def function_declarations() -> list[dict[str, Any]]:
    cells = {
        "type": "object",
        "properties": {
            "sheet": {"type": "string"},
            "cells": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 100,
            },
        },
        "required": ["sheet", "cells"],
    }
    range_read = {
        "type": "object",
        "properties": {
            "sheet": {"type": "string"},
            "a1_range": {"type": "string"},
        },
        "required": ["sheet", "a1_range"],
    }
    catalog_search = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "category": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }
    warehouse_search = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }
    declarations = [
        {"name": "get_source_workbook_metadata", "parameters": {"type": "object", "properties": {}}},
        {"name": "get_target_workbook_metadata", "parameters": {"type": "object", "properties": {}}},
        {"name": "read_source_cells", "parameters": cells},
        {"name": "read_target_cells", "parameters": cells},
        {"name": "read_source_range", "parameters": range_read},
        {"name": "read_target_range", "parameters": range_read},
        {"name": "search_material_catalog", "parameters": catalog_search},
        {"name": "search_warehouse_catalog", "parameters": warehouse_search},
    ]
    evidence = {
        "type": "object",
        "properties": {
            "sheet": {"type": "string"},
            "cell": {"type": "string"},
            "evidence_hash": {"type": "string"},
            "closing_value": {"type": "number"},
            "opening_value": {"type": "number"},
        },
        "required": ["sheet", "cell", "evidence_hash"],
    }
    operation = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["set_cell", "clear_cell"]},
            "material_id": {"type": "string"},
            "warehouse_id": {"type": "string"},
            "value": {"type": "number"},
            "source": evidence,
            "target": evidence,
            "semantic_context": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "rule": {"type": "string"},
                },
            },
        },
        "required": ["type", "target"],
    }
    issue = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
        },
    }
    declarations.append(
        {
            "name": "submit_carry_forward_plan",
            "description": "Submit the evidence-backed carry-forward plan. Use set_cell only with exact source and target evidence; use clear_cell only for validated reset cells.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {"type": "array", "items": operation},
                    "issues": {"type": "array", "items": issue},
                },
                "required": ["operations", "issues"],
            },
        }
    )
    return declarations


class GeminiCarryForwardPlanner:
    def __init__(self, sessions: sessionmaker[Session], gateway: RuntimeInventoryGeminiGateway, *, enabled: bool, deployment_allowed_models: tuple[str, ...] = (), client_factory=GoogleSheetsInventoryClient, token_resolver=get_connection_access_token):
        self.sessions, self.gateway, self.enabled, self.client_factory, self.token_resolver = sessions, gateway, enabled, client_factory, token_resolver
        self.deployment_allowed_models = tuple(dict.fromkeys(deployment_allowed_models))
    def _runtime(self, tenant_id: str) -> tuple[str, tuple[str, ...]]:
        if not self.enabled: raise CarryForwardReviewRequired("inventory_ai_disabled")
        with self.sessions() as session: control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
        if control is None or not control.enabled: raise CarryForwardReviewRequired("inventory_ai_disabled")
        if control.emergency_stop: raise CarryForwardReviewRequired("inventory_ai_emergency_stop")
        models = tuple(dict.fromkeys(str(v) for v in (control.allowed_models_json or []) if v))
        if self.deployment_allowed_models:
            allowed = set(self.deployment_allowed_models)
            models = tuple(model for model in models if model in allowed)
        if control.provider != "gemini" or not models: raise CarryForwardReviewRequired("inventory_ai_model_not_allowed")
        return control.provider, models
    def plan(self, *, tenant_id: str, previous_gemini_file_id: str, shared_workbook_id: str, prompt: str, connection_id: str = "") -> CarryForwardPlan:
        provider, models = self._runtime(tenant_id)
        if not connection_id:
            raise CarryForwardReviewRequired("carry_forward_google_connection_missing")
        value = self.token_resolver(connection_id)
        access_token = asyncio.run(value) if hasattr(value, "__await__") else str(value)
        contents: list[dict[str, Any]] = [{
            "role": "user",
            "parts": [{
                "text": (
                    prompt
                    + "\nThe server has bound roles previous_gemini and shared_current. "
                    "Use the read-only tools. Search material and warehouse catalogs with "
                    "specific queries instead of requesting broad catalogs. "
                    "Call submit_carry_forward_plan exactly once."
                )
            }],
        }]
        with self.client_factory(access_token) as google:
            host = CarryForwardToolHost(
                tenant_id=tenant_id,
                source_id=previous_gemini_file_id,
                target_id=shared_workbook_id,
                google=google,
                sessions=self.sessions,
            )
            active_model: str | None = None
            rounds = 0

            def attach_context(error: Exception) -> Exception:
                try:
                    setattr(
                        error,
                        "inventory_carry_forward_audit_context",
                        host.audit_snapshot(model=active_model, rounds=rounds),
                    )
                except Exception:
                    pass
                return error

            last_retryable_error: InventoryAiGatewayError | None = None
            try:
                for model in models:
                    active_model = model
                    try:
                        for _round in range(1, 13):
                            rounds += 1
                            turn = self.gateway.generate_tool_turn(
                                tenant_id=tenant_id,
                                contents=contents,
                                function_declarations=function_declarations(),
                                provider=provider,
                                model=model,
                            )
                            contents.append(dict(turn.content))
                            if not turn.calls:
                                break
                            responses = []
                            for call in turn.calls:
                                result = host.execute(call.name, call.arguments)
                                responses.append(
                                    {"functionResponse": {"name": call.name, "response": result}}
                                )
                            contents.append({"role": "user", "parts": responses})
                            if host.plan is not None:
                                return CarryForwardPlan(
                                    host.plan.rows,
                                    host.plan.issues,
                                    host.plan.legacy_metadata,
                                    host.plan.contract_version,
                                    host.audit_snapshot(model=active_model, rounds=rounds),
                                )
                    except InventoryAiGatewayError as exc:
                        if exc.retryable:
                            last_retryable_error = exc
                            continue
                        wrapped = CarryForwardReviewRequired(exc.code)
                        attach_context(wrapped)
                        raise wrapped from exc
            except Exception as exc:
                attach_context(exc)
                raise
            if last_retryable_error is not None:
                wrapped = CarryForwardReviewRequired(last_retryable_error.code)
                setattr(wrapped, "retryable", True)
                attach_context(wrapped)
                raise wrapped from last_retryable_error
            error = CarryForwardReviewRequired("carry_forward_plan_not_submitted")
            attach_context(error)
            raise error


def build_carry_forward_planner(*, session_factory: sessionmaker[Session], settings: Settings | None = None, client_factory=GoogleSheetsInventoryClient, token_resolver=get_connection_access_token) -> GeminiCarryForwardPlanner:
    runtime = settings or get_settings()
    return GeminiCarryForwardPlanner(session_factory, RuntimeInventoryGeminiGateway(InventoryGeminiCredentialResolver(session_factory, runtime), timeout_seconds=runtime.INVENTORY_AI_TIMEOUT_SECONDS), enabled=runtime.INVENTORY_AI_ENABLED, deployment_allowed_models=runtime.inventory_ai_allowed_models, client_factory=client_factory, token_resolver=token_resolver)
