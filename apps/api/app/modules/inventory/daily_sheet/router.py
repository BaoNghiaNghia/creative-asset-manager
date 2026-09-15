from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.daily_sheet.config import GeminiToolSheetAgentConfig, parse_daily_sheet_config
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.permissions import INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION, INVENTORY_READ_PERMISSION
from app.modules.inventory.persistence_model import InventorySettingsModel
from app.modules.inventory.daily_sheet.prompts import PROMPT_TYPES, InventoryPromptConflict, InventoryPromptResolver, InventoryPromptStorageUnavailable
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler

router = APIRouter(prefix="/daily-sheet", tags=["inventory-daily-sheet"])

class DailySheetSettingsRequest(BaseModel):
    image_pipeline_enabled: bool = True
    daily_sheet_automation_enabled: bool = False
    working_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    archive_root_folder_id: str | None = Field(default=None, max_length=2048)
    template_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    target_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    snapshot_time_local: str = Field(default="23:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    reconcile_time_local: str = Field(default="23:55", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    carry_forward_time_local: str = Field(default="05:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Ho_Chi_Minh"
    config: dict = Field(default_factory=dict)

class DiscoveryRequest(BaseModel):
    working_spreadsheet_file_id: str = Field(min_length=1, max_length=2048)

class RunRequest(BaseModel):
    business_date: date | None = None
    dry_run: bool = False

class V4RunRequest(BaseModel):
    business_date: date | None = None
    apply_mode: str = "shadow"

class BaselineRequest(BaseModel):
    snapshot_id: str
class PromptDraftRequest(BaseModel):
    content: str = Field(max_length=20_000)

def _service() -> InventoryDailySheetService:
    return InventoryDailySheetService(
        SessionLocal, semantic_analyzer=build_daily_sheet_semantic_analyzer(session_factory=SessionLocal)
    )
def _prompts() -> InventoryPromptResolver: return InventoryPromptResolver(SessionLocal)
def _prompt_type(value: str) -> str:
    if value not in PROMPT_TYPES: raise HTTPException(422, detail={"code":"invalid_inventory_prompt_type"})
    return value
def _legacy_goals(tenant_id: str) -> list[str]:
    with SessionLocal() as session:
        row=session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id==tenant_id))
        agent=((row.daily_sheet_config_json or {}).get("agent") or {}) if row else {}
        return list(agent.get("business_goal") or [])

@router.get("/prompts")
def get_prompts(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    return {"prompts": [_prompts().state(principal.active_tenant_id, kind, _legacy_goals(principal.active_tenant_id) if kind == "daily_gemini_processing" else None) for kind in sorted(PROMPT_TYPES)]}
@router.get("/prompts/{prompt_type}/versions")
def get_prompt_versions(prompt_type: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    return {"versions": _prompts().versions(principal.active_tenant_id, _prompt_type(prompt_type))}
@router.post("/prompts/{prompt_type}/drafts")
def create_prompt_draft(prompt_type: str, body: PromptDraftRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    try: return _prompts().draft(principal.active_tenant_id, _prompt_type(prompt_type), body.content, principal.user_id)
    except InventoryPromptConflict as exc: raise HTTPException(409, detail={"code":exc.code}) from exc
    except InventoryPromptStorageUnavailable as exc: raise HTTPException(503, detail={"code":exc.code}) from exc
    except ValueError as exc: raise HTTPException(422, detail={"code":str(exc)}) from exc
@router.post("/prompts/{prompt_type}/drafts/{prompt_id}/activate")
def activate_prompt(prompt_type: str, prompt_id: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    try: return _prompts().activate(principal.active_tenant_id, _prompt_type(prompt_type), prompt_id, principal.user_id)
    except InventoryPromptConflict as exc: raise HTTPException(409, detail={"code":exc.code}) from exc
    except InventoryPromptStorageUnavailable as exc: raise HTTPException(503, detail={"code":exc.code}) from exc
    except LookupError as exc: raise HTTPException(404, detail={"code":str(exc)}) from exc
@router.post("/prompts/{prompt_type}/versions/{prompt_id}/restore")
def restore_prompt(prompt_type: str, prompt_id: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    try: return _prompts().restore(principal.active_tenant_id, _prompt_type(prompt_type), prompt_id, principal.user_id)
    except LookupError as exc: raise HTTPException(404, detail={"code":str(exc)}) from exc
@router.post("/prompts/{prompt_type}/reset")
def reset_prompt(prompt_type: str, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    _prompts().reset(principal.active_tenant_id, _prompt_type(prompt_type), principal.user_id); return _prompts().state(principal.active_tenant_id, prompt_type, _legacy_goals(principal.active_tenant_id) if prompt_type == "daily_gemini_processing" else None)

def _business_date(tenant_id: str, supplied: date | None) -> date:
    if supplied: return supplied
    with SessionLocal() as session:
        settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id))
        timezone_name = settings.timezone if settings else "Asia/Ho_Chi_Minh"
    return datetime.now(ZoneInfo(timezone_name)).date()

def _settings_view(row):
    if row is None: return None
    return {
        "image_pipeline_enabled": row.image_pipeline_enabled,
        "daily_sheet_automation_enabled": row.daily_sheet_automation_enabled,
        "working_spreadsheet_file_id": row.daily_working_spreadsheet_file_id,
        "archive_root_folder_id": row.daily_archive_root_folder_id,
        "template_spreadsheet_file_id": row.daily_template_spreadsheet_file_id,
        "target_spreadsheet_file_id": row.daily_target_spreadsheet_file_id,
        "snapshot_time_local": row.daily_snapshot_time_local,
        "reconcile_time_local": row.daily_reconcile_time_local,
        "carry_forward_time_local": row.daily_carry_forward_time_local,
        "timezone": row.timezone,
        "config": row.daily_sheet_config_json,
    }

@router.get("/configuration")
def get_configuration(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    with SessionLocal() as session:
        return _settings_view(session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == principal.active_tenant_id)))

@router.put("/configuration")
def update_configuration(body: DailySheetSettingsRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    parsed_config = None
    try:
        if body.config:
            parsed_config = parse_daily_sheet_config(body.config)
        ZoneInfo(body.timezone)
    except Exception as exc:
        raise HTTPException(422, detail={"code": "invalid_daily_sheet_configuration", "message": str(exc)}) from exc
    if body.daily_sheet_automation_enabled and not (
        body.carry_forward_time_local <= body.snapshot_time_local <= body.reconcile_time_local
    ):
        raise HTTPException(422, detail={"code": "inventory_schedule_order_invalid"})
    with SessionLocal() as session:
        row = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == principal.active_tenant_id))
        if row is None: raise HTTPException(409, detail={"code": "inventory_settings_required"})
        row.image_pipeline_enabled = body.image_pipeline_enabled
        row.daily_sheet_automation_enabled = False
        row.daily_working_spreadsheet_file_id = body.working_spreadsheet_file_id
        row.daily_archive_root_folder_id = body.archive_root_folder_id
        row.daily_template_spreadsheet_file_id = body.template_spreadsheet_file_id
        row.daily_target_spreadsheet_file_id = body.target_spreadsheet_file_id
        row.daily_snapshot_time_local = body.snapshot_time_local
        row.daily_reconcile_time_local = body.reconcile_time_local
        row.daily_carry_forward_time_local = body.carry_forward_time_local
        row.daily_sheet_config_json = body.config
        row.timezone = body.timezone
        session.commit()
    if body.daily_sheet_automation_enabled:
        if isinstance(parsed_config, GeminiToolSheetAgentConfig) and parsed_config.agent.apply_mode != "auto":
            raise HTTPException(422, detail={"code": "gemini_tool_sheet_agent_scheduler_requires_auto_mode"})
        report = _service().validate_configuration(principal.active_tenant_id)
        if not report["valid"]:
            raise HTTPException(422, detail={"code": "daily_sheet_validation_failed", "report": report})
        with SessionLocal() as session:
            row = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == principal.active_tenant_id))
            row.daily_sheet_automation_enabled = True
            session.commit()
    return get_configuration(principal)

@router.get("/status")
def get_status(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    return _service().status(principal.active_tenant_id)

@router.get("/lifecycle-history")
def get_lifecycle_history(page: int = 1, page_size: int = 25, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    try:
        return _service().lifecycle_history(principal.active_tenant_id, page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.post("/lifecycle-history/{business_date}/morning-reset/rerun")
def rerun_morning_reset(business_date: date, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    try:
        return InventoryDailyScheduler(SessionLocal).retry_v4_morning_reset(principal.active_tenant_id, business_date)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
@router.post("/discover")
def discover_workbook(body: DiscoveryRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try:
        return _service().discover(principal.active_tenant_id, body.working_spreadsheet_file_id)
    except Exception as exc:
        raise HTTPException(422, detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}) from exc

@router.post("/validate-config")
def validate_config(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    return _service().validate_configuration(principal.active_tenant_id)

@router.post("/snapshot/run")
def run_snapshot(body: RunRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    service = _service()
    try:
        if service.is_agent_v4_configured(principal.active_tenant_id):
            raise HTTPException(
                409,
                detail={
                    "code": "gemini_tool_sheet_agent_use_run_endpoint",
                    "message": "Use /daily-sheet/agent-v4/run for Gemini Tool Sheet Agent V4.",
                },
            )
        if service.is_agent_v3_configured(principal.active_tenant_id):
            raise HTTPException(
                409,
                detail={
                    "code": "gemini_sheet_agent_use_plan_endpoint",
                    "message": "Use /daily-sheet/agent/plan for Gemini Sheet Agent V3.",
                },
            )
        row = service.snapshot_and_reset(
            principal.active_tenant_id,
            _business_date(principal.active_tenant_id, body.business_date),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            409,
            detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)},
        ) from exc
    return {"id": row.id, "business_date": row.business_date, "status": row.status, "snapshot_file_id": row.snapshot_file_id}

@router.post("/agent/plan")
def plan_agent_run(body: RunRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    service = _service()
    try:
        if not service.is_agent_v3_configured(principal.active_tenant_id):
            raise HTTPException(
                409,
                detail={
                    "code": "gemini_sheet_agent_not_configured",
                    "message": "Gemini Sheet Agent V3 is not configured.",
                },
            )
        return service.plan_agent_run(
            principal.active_tenant_id,
            _business_date(principal.active_tenant_id, body.business_date),
            dry_run=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            409,
            detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)},
        ) from exc

@router.post("/agent-v4/run")
def run_agent_v4(
    body: V4RunRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    service = _service()
    try:
        if not service.is_agent_v4_configured(principal.active_tenant_id):
            raise HTTPException(
                409,
                detail={"code": "gemini_tool_sheet_agent_not_configured"},
            )
        return service.run_agent_v4(
            principal.active_tenant_id,
            _business_date(principal.active_tenant_id, body.business_date),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            409,
            detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)},
        ) from exc

@router.post("/agent-v4/rerun-current")
def rerun_agent_v4_current(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    service = _service()
    try:
        if not service.is_agent_v4_configured(principal.active_tenant_id):
            raise HTTPException(409, detail={"code": "gemini_tool_sheet_agent_not_configured"})
        return service.rerun_agent_v4_current(
            principal.active_tenant_id, _business_date(principal.active_tenant_id, None)
        )
    except HTTPException:
        raise
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        if str(exc) == "inventory_gemini_manual_run_in_progress":
            code = "inventory_gemini_manual_run_in_progress"
        elif "not ready" in str(exc).lower():
            code = "daily_gemini_workbook_not_ready"
        raise HTTPException(409, detail={"code": code, "message": str(exc)}) from exc

@router.post("/reconcile/run")
def run_reconcile(body: RunRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().reconcile(principal.active_tenant_id, _business_date(principal.active_tenant_id, body.business_date), dry_run=body.dry_run)
    except Exception as exc: raise HTTPException(409, detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}) from exc

@router.post("/baseline")
def set_baseline(body: BaselineRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().set_baseline(principal.active_tenant_id, body.snapshot_id)
    except LookupError as exc: raise HTTPException(404, detail={"code": str(exc)}) from exc
