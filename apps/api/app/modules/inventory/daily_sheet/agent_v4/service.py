from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.model import InventoryAiControlModel

from .contracts import V4AgentRunResult
from .tools import V4AgentSafetyError, V4WorkbookToolHost, function_declarations

logger = logging.getLogger("cam.inventory.daily_sheet.agent_v4")

V4_PROMPT_VERSION = "inventory-sheet-tool-agent-v4-2"
V4_HIGH_LEVEL_GOAL = """Act as an investigative Inventory workbook operator in a safety-controlled environment.
Start with workbook metadata, then read enough exact cell evidence to understand the workbook's own labels, structure, formulas, values, operational rows and field relationships.
Do not assume the first range is sufficient, and do not assume fixed headers, columns, rows or ranges.
Inspect row-local anomalies and relationships. Distinguish blank from zero, coherent raw structured values from structurally suspicious data, and missing evidence from evidence that no action is required.
When material or item identities are relevant to the configured business goal, inspect the tenant material catalog before finalizing. Do not perform fuzzy matching outside the model; MATCH_EXISTING must cite an exact active catalog material_id.
Never invent a value. In automatic mode, process every report material independently: update only materials that can be matched to an existing editable warehouse row. First prefer an exact normalized name or confirmed catalog alias. A close name is allowed only after reading the candidate warehouse row and confirming compatible category, unit, and operational context; cite evidence from both the report and selected warehouse row. Process matched materials even when other report rows are unmatched.
For an unmatched material, do not create a warehouse row, do not alter that report row, and do not stage AMBIGUOUS_MATERIAL_MAPPING. Instead stage a non-review informational issue with code UNMAPPED_MATERIAL_SKIPPED. An unmatched line must not block evidence-backed operations for other materials.
Use exact_copy provenance whenever a value is copied without transformation. Cite the exact evidence hash returned by a read tool for every assessment observation, edit, issue and material action.
Before stage_edits, call submit_workbook_assessment. If more evidence is needed, read it and submit an updated complete assessment before staging.
Perform a silent completeness check, then call stage_edits exactly once. A ready no-op plan requires a grounded assessment explaining why no action or review is needed.
Preserve formulas, protected or merged structure, workbook labels and exact raw quantity representations.
The host enforces evidence revalidation and mechanical safeguards before any configured live write.
Do not expose hidden chain-of-thought, credentials, API requests or full sensitive provider responses."""


class V4AgentUnavailable(RuntimeError):
    code = "inventory_sheet_agent_v4_unavailable"


class InventoryDailySheetV4Service:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        gateway: RuntimeInventoryGeminiGateway,
        context_provider: Callable[[str], Any],
        client_factory: Callable[..., Any],
        token_resolver: Callable[[str], Any],
        enabled: bool,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.context_provider = context_provider
        self.client_factory = client_factory
        self.token_resolver = token_resolver
        self.enabled = enabled

    def _runtime(self, tenant_id: str) -> tuple[str, str]:
        if not self.enabled:
            raise V4AgentUnavailable("inventory_ai_disabled")
        with self.session_factory() as session:
            control = session.scalar(
                select(InventoryAiControlModel).where(
                    InventoryAiControlModel.tenant_id == tenant_id
                )
            )
        if control is None or not control.enabled:
            raise V4AgentUnavailable("inventory_ai_disabled")
        if control.emergency_stop:
            raise V4AgentUnavailable("inventory_ai_emergency_stop")
        models = tuple(str(value) for value in (control.allowed_models_json or ()) if value)
        if control.provider != "gemini" or not models:
            raise V4AgentUnavailable("inventory_ai_model_not_allowed")
        return control.provider, models[0]

    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if hasattr(value, "__await__") else str(value)

    def run(
        self, tenant_id: str, business_date: date, *, slot_kind: str | None = None
    ) -> V4AgentRunResult:
        context = self.context_provider(tenant_id)
        return self.run_shadow(
            tenant_id,
            business_date,
            apply_mode=context.config.agent.apply_mode,
            slot_kind=slot_kind,
        )

    def run_shadow(
        self, tenant_id: str, business_date: date, *, apply_mode: str = "shadow",
        slot_kind: str | None = None,
    ) -> V4AgentRunResult:
        if apply_mode not in {"shadow", "review", "auto"}:
            raise V4AgentSafetyError("invalid_apply_mode")
        context = self.context_provider(tenant_id)
        config = context.config
        configured_file_id = config.source.spreadsheet_file_id
        if configured_file_id and configured_file_id != context.working_file_id:
            raise V4AgentSafetyError("spreadsheet_not_authorized")
        provider, model = self._runtime(tenant_id)
        contents: list[dict[str, Any]] = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": V4_HIGH_LEVEL_GOAL
                        + "\n"
                        + json.dumps(
                            {
                                "prompt_version": V4_PROMPT_VERSION,
                                "business_date": business_date.isoformat(),
                                "slot_kind": slot_kind,
                                "apply_mode": apply_mode,
                                "spreadsheet_file_id": context.working_file_id,
                                "allowed_sheets": config.source.allowed_sheets,
                                "business_goal": config.agent.business_goal,
                                "allow_auto_evidence_backed_transforms": config.agent.allow_auto_evidence_backed_transforms,
                                "limits": {
                                    "max_tool_rounds": config.agent.max_tool_rounds,
                                    "max_read_calls": config.agent.max_read_calls,
                                    "max_read_cells": config.agent.max_read_cells,
                                    "max_edit_operations": config.agent.max_edit_operations,
                                },
                            },
                            sort_keys=True,
                        )
                    }
                ],
            }
        ]
        google = self.client_factory(self._token(context.connection_id))
        host = V4WorkbookToolHost(
            tenant_id=tenant_id,
            spreadsheet_file_id=context.working_file_id,
            allowed_sheets=config.source.allowed_sheets,
            google=google,
            session_factory=self.session_factory,
            max_read_calls=config.agent.max_read_calls,
            max_read_cells=config.agent.max_read_cells,
            max_edit_operations=config.agent.max_edit_operations,
            allow_auto_transforms=(
                apply_mode == "auto"
                and config.agent.allow_auto_evidence_backed_transforms
            ),
        )
        rounds = 0
        try:
            for rounds in range(1, config.agent.max_tool_rounds + 1):
                turn = self.gateway.generate_tool_turn(
                    tenant_id=tenant_id,
                    contents=contents,
                    function_declarations=function_declarations(),
                    provider=provider,
                    model=model,
                )
                contents.append(dict(turn.content))
                if not turn.calls:
                    raise V4AgentUnavailable("inventory_sheet_agent_v4_missing_tool_call")
                response_parts = []
                for call in turn.calls:
                    result = host.execute(call.name, call.arguments)
                    response_parts.append(
                        {
                            "functionResponse": {
                                "name": call.name,
                                "response": result,
                            }
                        }
                    )
                contents.append({"role": "user", "parts": response_parts})
                if host.staged is not None:
                    break
            if host.staged is None:
                raise V4AgentUnavailable("inventory_sheet_agent_v4_round_limit")
            staged_payload = host.staged.model_dump(mode="json")
            digest = hashlib.sha256(
                json.dumps(
                    staged_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            execution = (
                host.apply_staged()
                if apply_mode == "auto"
                else {"status": "shadow", "writes": 0, "set_count": 0, "clear_count": 0, "verification_status": "not_executed"}
            )
            status = (
                "blocked"
                if host.staged.status == "blocked"
                else "review_required"
                if host.staged.requires_review or host.staged.status == "review_required"
                else "completed"
                if execution["status"] == "completed"
                else "shadow"
            )
            run_id = hashlib.sha256(
                f"inventory-v4:{tenant_id}:{business_date.isoformat()}:{slot_kind or 'manual'}:{digest}".encode(
                    "utf-8"
                )
            ).hexdigest()
            result = V4AgentRunResult(
                apply_mode=apply_mode,
                status=status,
                run_id=run_id,
                tenant_id=tenant_id,
                spreadsheet_file_id=context.working_file_id,
                business_date=business_date.isoformat(),
                tool_rounds=rounds,
                read_calls=host.read_calls,
                read_cells=host.read_cells,
                plan_hash=digest,
                staged=host.staged,
                tools_called=[item["tool"] for item in host.tool_trace],
                assessment_present=host.assessment is not None,
                catalog_read=any(
                    item["tool"] == "get_material_catalog"
                    for item in host.tool_trace
                ),
                ranges_read=[
                    item["range"]
                    for item in host.tool_trace
                    if item["tool"] == "read_range" and item.get("range")
                ],
                tool_trace=host.tool_trace,
                writes=execution["writes"],
            )
            logger.info(
                "inventory_sheet_agent_v4_completed",
                extra={
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "spreadsheet_id": context.working_file_id,
                    "business_date": business_date.isoformat(),
                    "tool_rounds": rounds,
                    "read_calls": host.read_calls,
                    "read_cells": host.read_cells,
                    "operation_count": len(host.staged.operations),
                    "tools_called": [item["tool"] for item in host.tool_trace],
                    "assessment_present": host.assessment is not None,
                    "catalog_read": any(
                        item["tool"] == "get_material_catalog"
                        for item in host.tool_trace
                    ),
                    "plan_hash": digest,
                    "status": status,
                    "model": model,
                    "writes": execution["writes"],
                },
            )
            return result
        finally:
            google.close()


def build_daily_sheet_v4_service(
    *,
    session_factory: sessionmaker[Session],
    context_provider: Callable[[str], Any],
    client_factory: Callable[..., Any],
    token_resolver: Callable[[str], Any],
    settings: Settings | None = None,
) -> InventoryDailySheetV4Service:
    runtime_settings = settings or get_settings()
    gateway = RuntimeInventoryGeminiGateway(
        InventoryGeminiCredentialResolver(session_factory, runtime_settings),
        timeout_seconds=runtime_settings.INVENTORY_AI_TIMEOUT_SECONDS,
    )
    return InventoryDailySheetV4Service(
        session_factory=session_factory,
        gateway=gateway,
        context_provider=context_provider,
        client_factory=client_factory,
        token_resolver=token_resolver,
        enabled=runtime_settings.INVENTORY_AI_ENABLED,
    )
