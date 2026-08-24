from __future__ import annotations
import asyncio
import logging
import time
from datetime import date
from typing import Any, Callable
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import Settings, get_settings
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.persistence_model import InventoryItemModel
from .contracts import AgentRunResult, EditPlan, WorkbookSnapshot
from .executor import GoogleSheetEditPlanExecutor, plan_hash
from .guard import SheetAgentSafetyGuard
from .planner import GeminiSheetAgentPlanner
from .snapshot import build_workbook_snapshot

logger = logging.getLogger("cam.inventory.daily_sheet.agent")


class SheetAgentApplyNotAllowed(RuntimeError):
    code = "sheet_agent_apply_not_allowed"


class InventoryDailySheetAgentService:
    def __init__(self, *, planner, snapshot_loader: Callable[[str], WorkbookSnapshot], executor_factory: Callable[[str, Callable[[], WorkbookSnapshot]], GoogleSheetEditPlanExecutor], config_loader: Callable[[str], Any], guard: SheetAgentSafetyGuard) -> None:
        self.planner = planner
        self.snapshot_loader = snapshot_loader
        self.executor_factory = executor_factory
        self.config_loader = config_loader
        self.guard = guard

    def _guard(self, tenant_id: str, plan: EditPlan, snapshot: WorkbookSnapshot, config):
        return self.guard.validate(tenant_id=tenant_id, plan=plan, snapshot=snapshot, allowed_range=config.source.a1_range, max_operations=config.safety.max_edit_operations, allow_structure_changes=config.safety.allow_structure_changes, allow_formula_changes=config.safety.allow_formula_changes)

    def plan_agent_run(self, tenant_id: str, business_date: date, *, dry_run: bool = True) -> AgentRunResult:
        started = time.monotonic()
        config = self.config_loader(tenant_id)
        snapshot = self.snapshot_loader(tenant_id)
        plan, model = self.planner.plan(tenant_id=tenant_id, business_date=business_date.isoformat(), snapshot=snapshot, business_goal=config.agent.business_goal)
        guard = self._guard(tenant_id, plan, snapshot, config)
        digest = plan_hash(plan)
        status = "blocked" if not guard.accepted else ("review_required" if guard.requires_review or config.agent.apply_mode == "review" else "shadow")
        execution = None
        if config.agent.apply_mode == "auto" and not dry_run and guard.accepted and not guard.requires_review and plan.status == "ready" and not plan.requires_review:
            execution = self.apply_agent_plan(tenant_id, plan, expected_plan_hash=digest, expected_source_hash=snapshot.source_hash)
            status = "completed" if execution.status == "completed" else "blocked"
        result = AgentRunResult(status=status, tenant_id=tenant_id, business_date=business_date.isoformat(), source_hash=snapshot.source_hash, plan_hash=digest, plan=plan, operation_count=len(plan.operations), set_operations=guard.set_operations, clear_operations=guard.clear_operations, review_operations=[item for item in plan.operations if item.requires_review or item.business_action == "data_repair"], blocked_reasons=guard.errors, issues=plan.issues, material_suggestions=plan.material_actions, execution=execution)
        logger.info("inventory_sheet_agent_planned", extra={"tenant_id": tenant_id, "spreadsheet_id": snapshot.spreadsheet_file_id, "source_hash": snapshot.source_hash, "plan_hash": digest, "operation_count": len(plan.operations), "review_count": len(guard.review_reasons), "status": result.status, "model": model, "duration_ms": int((time.monotonic() - started) * 1000)})
        return result

    def apply_agent_plan(self, tenant_id: str, plan: EditPlan, *, expected_plan_hash: str, expected_source_hash: str):
        config = self.config_loader(tenant_id)
        if config.agent.apply_mode == "shadow":
            raise SheetAgentApplyNotAllowed("sheet_agent_shadow_mode")
        snapshot = self.snapshot_loader(tenant_id)
        if plan_hash(plan) != expected_plan_hash or plan.source.source_hash != expected_source_hash:
            from .executor import StaleEditPlan
            raise StaleEditPlan("stale_edit_plan")
        guard = self._guard(tenant_id, plan, snapshot, config)
        executor = self.executor_factory(tenant_id, lambda: self.snapshot_loader(tenant_id))
        result = executor.execute(
            plan=plan,
            snapshot=snapshot,
            guard=guard,
            expected_plan_hash=expected_plan_hash,
            expected_source_hash=expected_source_hash,
        )
        logger.info(
            "inventory_sheet_agent_executed",
            extra={
                "tenant_id": tenant_id,
                "spreadsheet_id": snapshot.spreadsheet_file_id,
                "source_hash": result.source_hash,
                "plan_hash": result.plan_hash,
                "set_count": result.set_count,
                "clear_count": result.clear_count,
                "verification_status": result.verification_status,
                "status": result.status,
            },
        )
        return result

class ConfiguredAgentRuntime:
    def __init__(self, *, context_provider, client_factory, token_resolver, core_factory):
        self.context_provider, self.client_factory, self.token_resolver = context_provider, client_factory, token_resolver
        self._core_factory = core_factory
    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if hasattr(value, "__await__") else str(value)
    def _snapshot_with(self, google, context) -> WorkbookSnapshot:
        file_metadata = google.validate_native_spreadsheet(context.working_file_id)
        metadata = google.spreadsheet_metadata(context.working_file_id)
        raw = google.batch_get_values(context.working_file_id, [context.config.source.a1_range])
        formulas = google.batch_get_values(context.working_file_id, [context.config.source.a1_range], value_render_option="FORMULA")
        return build_workbook_snapshot(spreadsheet_file_id=context.working_file_id, file_metadata=file_metadata, spreadsheet_metadata=metadata, requested_range=context.config.source.a1_range, raw_block=raw[0] if raw else {}, formula_block=formulas[0] if formulas else {})
    def snapshot(self, tenant_id: str) -> WorkbookSnapshot:
        context = self.context_provider(tenant_id)
        with self.client_factory(self._token(context.connection_id)) as google:
            return self._snapshot_with(google, context)
    def executor(self, tenant_id: str, snapshot_loader):
        context = self.context_provider(tenant_id)
        google = self.client_factory(self._token(context.connection_id))
        base = GoogleSheetEditPlanExecutor(google=google, snapshot_loader=snapshot_loader)
        original = base.execute
        def execute(**kwargs):
            try: return original(**kwargs)
            finally: google.close()
        base.execute = execute
        return base

def build_daily_sheet_agent_service(*, session_factory: sessionmaker[Session], context_provider, client_factory, token_resolver, settings: Settings | None = None) -> InventoryDailySheetAgentService:
    runtime_settings = settings or get_settings()
    gateway = RuntimeInventoryGeminiGateway(InventoryGeminiCredentialResolver(session_factory, runtime_settings), timeout_seconds=runtime_settings.INVENTORY_AI_TIMEOUT_SECONDS)
    planner = GeminiSheetAgentPlanner(session_factory, gateway, enabled=runtime_settings.INVENTORY_AI_ENABLED)
    def valid_material(tenant_id: str, material_id: str) -> bool:
        with session_factory() as session:
            return session.scalar(select(InventoryItemModel.id).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.id == material_id, InventoryItemModel.active.is_(True))) is not None
    guard = SheetAgentSafetyGuard(material_validator=valid_material)
    runtime = ConfiguredAgentRuntime(context_provider=context_provider, client_factory=client_factory, token_resolver=token_resolver, core_factory=None)
    return InventoryDailySheetAgentService(
        planner=planner,
        snapshot_loader=runtime.snapshot,
        executor_factory=lambda tenant_id, loader: runtime.executor(tenant_id, loader),
        config_loader=lambda tenant_id: context_provider(tenant_id).config,
        guard=guard,
    )
