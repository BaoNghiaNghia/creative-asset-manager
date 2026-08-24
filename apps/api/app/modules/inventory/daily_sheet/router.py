from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.daily_sheet.config import parse_daily_sheet_config
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.permissions import INVENTORY_FINALIZE_PERMISSION, INVENTORY_READ_PERMISSION
from app.modules.inventory.persistence_model import InventorySettingsModel

router = APIRouter(prefix="/daily-sheet", tags=["inventory-daily-sheet"])

class DailySheetSettingsRequest(BaseModel):
    image_pipeline_enabled: bool = True
    daily_sheet_automation_enabled: bool = False
    working_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    archive_root_folder_id: str | None = Field(default=None, max_length=2048)
    template_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    target_spreadsheet_file_id: str | None = Field(default=None, max_length=2048)
    snapshot_time_local: str = Field(default="05:50", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    reconcile_time_local: str = Field(default="07:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Ho_Chi_Minh"
    config: dict = Field(default_factory=dict)

class DiscoveryRequest(BaseModel):
    working_spreadsheet_file_id: str = Field(min_length=1, max_length=2048)

class RunRequest(BaseModel):
    business_date: date | None = None
    dry_run: bool = False

class BaselineRequest(BaseModel):
    snapshot_id: str

def _service() -> InventoryDailySheetService:
    return InventoryDailySheetService(
        SessionLocal, semantic_analyzer=build_daily_sheet_semantic_analyzer(session_factory=SessionLocal)
    )

def _business_date(tenant_id: str, supplied: date | None) -> date:
    if supplied: return supplied
    with SessionLocal() as session:
        settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id))
        timezone_name = settings.timezone if settings else "Asia/Ho_Chi_Minh"
    return datetime.now(ZoneInfo(timezone_name)).date() - timedelta(days=1)

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
        "timezone": row.timezone,
        "config": row.daily_sheet_config_json,
    }

@router.get("/configuration")
def get_configuration(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    with SessionLocal() as session:
        return _settings_view(session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == principal.active_tenant_id)))

@router.put("/configuration")
def update_configuration(body: DailySheetSettingsRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try:
        if body.config: parse_daily_sheet_config(body.config)
        ZoneInfo(body.timezone)
    except Exception as exc:
        raise HTTPException(422, detail={"code": "invalid_daily_sheet_configuration", "message": str(exc)}) from exc
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
        row.daily_sheet_config_json = body.config
        row.timezone = body.timezone
        session.commit()
    if body.daily_sheet_automation_enabled:
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

@router.post("/reconcile/run")
def run_reconcile(body: RunRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().reconcile(principal.active_tenant_id, _business_date(principal.active_tenant_id, body.business_date), dry_run=body.dry_run)
    except Exception as exc: raise HTTPException(409, detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}) from exc

@router.post("/baseline")
def set_baseline(body: BaselineRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().set_baseline(principal.active_tenant_id, body.snapshot_id)
    except LookupError as exc: raise HTTPException(404, detail={"code": str(exc)}) from exc
