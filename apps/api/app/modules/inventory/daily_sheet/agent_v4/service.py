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

V4_PROMPT_VERSION = "inventory-sheet-tool-agent-v4-1"
V4_HIGH_LEVEL_GOAL = """Inspect the authorized workbook using tools and stage a safe end-of-day Inventory workbook update.
Infer all workbook and business semantics from exact cell evidence; Python has no business-schema knowledge.
Read adaptively and never assume fixed headers, columns, rows, or ranges.
Preserve formulas, protected/merged structure, workbook labels and exact raw quantity representations.
Blank is unknown and is never zero.
Use exact_copy provenance whenever a value is copied without transformation.
Cite the exact evidence hash returned by a read tool for every edit, issue, and material action.
Use the tenant material catalog only when needed. MATCH_EXISTING must name an exact catalog material_id; all other material actions require review.
Call stage_edits exactly once when sufficient evidence has been gathered. The host is shadow-only and will not mutate the workbook.
Do not return prose, credentials, API requests, or hidden reasoning."""


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

    def run_shadow(self, tenant_id: str, business_date: date) -> V4AgentRunResult:
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
                                "apply_mode": "shadow",
                                "spreadsheet_file_id": context.working_file_id,
                                "allowed_sheets": config.source.allowed_sheets,
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
            status = (
                "blocked"
                if host.staged.status == "blocked"
                else "review_required"
                if host.staged.requires_review or host.staged.status == "review_required"
                else "shadow"
            )
            result = V4AgentRunResult(
                status=status,
                tenant_id=tenant_id,
                spreadsheet_file_id=context.working_file_id,
                business_date=business_date.isoformat(),
                tool_rounds=rounds,
                read_calls=host.read_calls,
                read_cells=host.read_cells,
                plan_hash=digest,
                staged=host.staged,
            )
            logger.info(
                "inventory_sheet_agent_v4_shadow_completed",
                extra={
                    "tenant_id": tenant_id,
                    "spreadsheet_id": context.working_file_id,
                    "business_date": business_date.isoformat(),
                    "tool_rounds": rounds,
                    "read_calls": host.read_calls,
                    "read_cells": host.read_cells,
                    "operation_count": len(host.staged.operations),
                    "plan_hash": digest,
                    "status": status,
                    "model": model,
                    "writes": 0,
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
