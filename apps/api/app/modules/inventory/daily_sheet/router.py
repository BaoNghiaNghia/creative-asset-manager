from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.daily_sheet.config import GeminiToolSheetAgentConfig, parse_daily_sheet_config
from app.modules.inventory.daily_sheet.audit import InventoryOperationAuditService
from app.modules.inventory.daily_sheet.knowledge import InventoryKnowledgeError, InventoryKnowledgeService
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService, DailySheetConfigurationError, inventory_error_metadata
from app.modules.inventory.daily_sheet.google_client import DailySheetProviderError
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.permissions import INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION, INVENTORY_READ_PERMISSION
from app.modules.inventory.persistence_model import InventorySettingsModel
from app.modules.inventory.daily_sheet.prompts import PROMPT_TYPES, InventoryPromptConflict, InventoryPromptResolver, InventoryPromptStorageUnavailable
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
from app.modules.inventory.daily_sheet.replay_queue import HistoricalReplayQueue
from app.modules.inventory.daily_sheet.catinat_preview import CatinatPreviewError, load_catinat_preview
from app.modules.inventory.daily_sheet.catinat_shift_readiness import load_evening_readiness
from app.modules.inventory.daily.catinat_close_preflight import CatinatClosePreflightService
from app.modules.inventory.daily.catinat_safety_audit import read_only_shelf_life_transfer_audit
from app.modules.inventory.daily.catinat_catalog_mapping import CatinatCatalogMappingPreview
from app.modules.inventory.daily.catinat_count_confirmations import CatinatCountBlocked
from app.modules.inventory.daily.catinat_approval_router import router as catinat_approval_router
from app.modules.inventory.daily.catinat_certification_router import router as catinat_certification_router
from app.modules.inventory.daily.catinat_count_router import router as catinat_count_router
from app.modules.inventory.daily.catinat_transfer_router import router as catinat_transfer_router

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

class HistoricalReplayRequest(BaseModel):
    mode: str = Field(default="fresh_copy", pattern=r"^(fresh_copy|existing_copy)$")
    promote: bool = True

class MorningResetRecoveryApplyRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

class BaselineRequest(BaseModel):
    snapshot_id: str
class PromptDraftRequest(BaseModel):
    content: str = Field(max_length=20_000)

class KnowledgeDraftRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=20_000)
    scope_type: str = Field(default="workbook", min_length=1, max_length=32)
    scope_key: str = Field(default="*", min_length=1, max_length=255)
    structured_rule: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)

class KnowledgeRevisionRequest(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=32)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    scope_type: str | None = Field(default=None, min_length=1, max_length=32)
    scope_key: str | None = Field(default=None, min_length=1, max_length=255)
    structured_rule: dict | None = None
    evidence: list[dict] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

def _service() -> InventoryDailySheetService:
    return InventoryDailySheetService(
        SessionLocal, semantic_analyzer=build_daily_sheet_semantic_analyzer(session_factory=SessionLocal)
    )
def _prompts() -> InventoryPromptResolver: return InventoryPromptResolver(SessionLocal)
def _knowledge() -> InventoryKnowledgeService: return InventoryKnowledgeService(SessionLocal)
def _audit() -> InventoryOperationAuditService: return InventoryOperationAuditService(SessionLocal)
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

@router.get("/writeback-qa/preflight")
def inspect_disposable_inventory_writeback(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Only GET Drive/Sheets configuration and capabilities. No copy/write/trash."""
    try:
        return _service().preflight_disposable_writeback(principal.active_tenant_id)
    except DailySheetConfigurationError as exc:
        raise HTTPException(422, detail={"code": "inventory_qa_configuration_unavailable"}) from exc
    except DailySheetProviderError as exc:
        code = exc.code if exc.code in {
            "authorization", "scope_missing", "not_found", "invalid_file_type",
            "rate_limited", "transient_provider_error",
        } else "permanent_provider_error"
        status = 503 if code in {"rate_limited", "transient_provider_error"} else 422
        raise HTTPException(status, detail={"code": f"inventory_qa_{code}"}) from exc


@router.post("/writeback-qa")
def verify_disposable_inventory_writeback(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    """Explicit QA action: write on a fresh throwaway copy; never on the source."""
    try:
        return _service().verify_disposable_writeback(principal.active_tenant_id)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc


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
    if body.daily_sheet_automation_enabled:
        if isinstance(parsed_config, GeminiToolSheetAgentConfig) and parsed_config.agent.apply_mode != "auto":
            raise HTTPException(422, detail={"code": "gemini_tool_sheet_agent_scheduler_requires_auto_mode"})
        # Read-only external validation of the candidate configuration. Never
        # disable an existing daily schedule or persist an invalid workbook.
        candidate = {
            "daily_working_spreadsheet_file_id": body.working_spreadsheet_file_id,
            "daily_archive_root_folder_id": body.archive_root_folder_id,
            "daily_template_spreadsheet_file_id": body.template_spreadsheet_file_id,
            "daily_target_spreadsheet_file_id": body.target_spreadsheet_file_id,
            "daily_sheet_config_json": body.config,
        }
        report = _service().validate_configuration(
            principal.active_tenant_id,
            settings_override=candidate,
            timezone_override=body.timezone,
        )
        if not report["valid"]:
            raise HTTPException(422, detail={"code": "daily_sheet_validation_failed", "report": report})
    with SessionLocal() as session:
        row = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == principal.active_tenant_id))
        if row is None: raise HTTPException(409, detail={"code": "inventory_settings_required"})
        row.image_pipeline_enabled = body.image_pipeline_enabled
        row.daily_sheet_automation_enabled = body.daily_sheet_automation_enabled
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
    return get_configuration(principal)

@router.get("/catinat/closing-preview")
def get_catinat_closing_preview(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Read-only expected balances; never confirms a physical count or writes Sheets."""
    try:
        return load_catinat_preview(
            principal.active_tenant_id, business_date, sessions=SessionLocal,
        )
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/catinat/shift/readiness")
def get_catinat_shift_readiness(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Read-only physical count readiness for the three approved warehouse layouts."""
    try:
        return load_evening_readiness(
            principal.active_tenant_id, business_date, sessions=SessionLocal,
        )
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/catinat/catalog/mapping-preview")
def get_catinat_catalog_mapping_preview(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Read-only exact-match candidates, never approved catalog mappings."""
    try:
        return CatinatCatalogMappingPreview(SessionLocal).inspect(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: load_evening_readiness(
                principal.active_tenant_id, business_date, sessions=SessionLocal,
            ),
        )
    except (CatinatCountBlocked, CatinatPreviewError) as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/catinat/shift/safety-triage")
def get_catinat_safety_triage(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Batch/expiry, waste and handoff risks; strictly no accounting writes."""
    try:
        return read_only_shelf_life_transfer_audit(
            SessionLocal, tenant_id=principal.active_tenant_id,
            business_date=business_date,
            current_readiness=lambda: load_evening_readiness(
                principal.active_tenant_id, business_date, sessions=SessionLocal,
            ),
        )
    except (CatinatCountBlocked, CatinatPreviewError) as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/catinat/shift/closing-preflight")
def get_catinat_closing_preflight(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Aggregate signed-cell/transfer/approval blockers. Never closes a shift."""
    try:
        return CatinatClosePreflightService(SessionLocal).inspect(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: load_evening_readiness(
                principal.active_tenant_id, business_date, sessions=SessionLocal,
            ),
        )
    except (CatinatCountBlocked, CatinatPreviewError) as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/status")
def get_status(principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    return _service().status(principal.active_tenant_id)

@router.get("/lifecycle-history")
def get_lifecycle_history(page: int = 1, page_size: int = 25, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION))):
    try:
        return _service().lifecycle_history(principal.active_tenant_id, page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.get("/lifecycle-history/{business_date}/stages/{stage}/detail")
def get_lifecycle_stage_detail(
    business_date: date,
    stage: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _audit().stage_detail(principal.active_tenant_id, business_date, stage)
    except LookupError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.get("/knowledge/learning-status")
def get_inventory_learning_status(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    return _knowledge().learning_status(principal.active_tenant_id)


@router.get("/knowledge")
def get_inventory_knowledge(
    status: str | None = None,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    statuses = tuple(value.strip() for value in status.split(",") if value.strip()) if status else None
    try:
        return {"items": _knowledge().list(principal.active_tenant_id, statuses=statuses)}
    except InventoryKnowledgeError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.post("/knowledge")
def create_inventory_knowledge(
    body: KnowledgeDraftRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return _knowledge().create(
            principal.active_tenant_id,
            kind=body.kind,
            title=body.title,
            content=body.content,
            scope_type=body.scope_type,
            scope_key=body.scope_key,
            structured_rule=body.structured_rule,
            evidence=body.evidence,
            confidence=body.confidence,
            actor_id=principal.user_id,
        )
    except InventoryKnowledgeError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.post("/knowledge/{entry_id}/revisions")
def revise_inventory_knowledge(
    entry_id: str,
    body: KnowledgeRevisionRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return _knowledge().revise(
            principal.active_tenant_id,
            entry_id,
            kind=body.kind,
            title=body.title,
            content=body.content,
            scope_type=body.scope_type,
            scope_key=body.scope_key,
            structured_rule=body.structured_rule,
            evidence=body.evidence,
            confidence=body.confidence,
            actor_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except InventoryKnowledgeError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc

@router.post("/knowledge/{entry_id}/activate")
def activate_inventory_knowledge(
    entry_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return _knowledge().activate(principal.active_tenant_id, entry_id, principal.user_id)
    except LookupError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except InventoryKnowledgeError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

@router.post("/knowledge/{entry_id}/reject")
def reject_inventory_knowledge(
    entry_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return _knowledge().reject(principal.active_tenant_id, entry_id, principal.user_id)
    except LookupError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    except InventoryKnowledgeError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

@router.post("/lifecycle-history/{business_date}/morning-reset/rerun")
def rerun_morning_reset(business_date: date, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION))):
    try:
        return InventoryDailyScheduler(SessionLocal).retry_v4_morning_reset(principal.active_tenant_id, business_date)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

@router.post("/lifecycle-history/{business_date}/morning-reset/recovery/preview")
def preview_morning_reset_recovery(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return InventoryDailyScheduler(SessionLocal).preview_v4_morning_reset_recovery(
            principal.active_tenant_id,
            business_date,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from exc
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        metadata = inventory_error_metadata(code)
        raise HTTPException(
            409,
            detail={
                "code": code,
                "message": str(exc),
                "error_category": metadata["category"],
                "retryable": metadata["retryable"],
            },
        ) from exc

@router.post("/lifecycle-history/{business_date}/morning-reset/recovery/apply")
def apply_morning_reset_recovery(
    business_date: date,
    body: MorningResetRecoveryApplyRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return InventoryDailyScheduler(SessionLocal).apply_v4_morning_reset_recovery(
            principal.active_tenant_id,
            business_date,
            plan_hash=body.plan_hash,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from exc
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        metadata = inventory_error_metadata(code)
        raise HTTPException(
            409,
            detail={
                "code": code,
                "message": str(exc),
                "error_category": metadata["category"],
                "retryable": metadata["retryable"],
            },
        ) from exc
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

@router.post("/lifecycle-history/{business_date}/gemini/replay/queue")
def enqueue_historical_gemini(
    business_date: date,
    body: HistoricalReplayRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    try:
        return HistoricalReplayQueue(SessionLocal).enqueue(
            principal.active_tenant_id, business_date, body.mode, body.promote,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc


@router.get("/gemini/replay/diagnostics")
def inspect_historical_gemini_dates(
    dates: str | None = None,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Date-scoped, tenant-confined SELECT-only replay and Reset evidence."""
    parts = dates.split(",") if dates else ()
    if (
        not 1 <= len(parts) <= 8
        or len(set(parts)) != len(parts)
        or any(len(part) != 10 for part in parts)
    ):
        raise HTTPException(422, detail={"code": "inventory_replay_diagnostic_dates_invalid"})
    try:
        parsed = tuple(date.fromisoformat(part) for part in parts)
        if any(day.isoformat() != part for day, part in zip(parsed, parts)):
            raise ValueError("non-canonical date")
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "inventory_replay_diagnostic_dates_invalid"}
        ) from exc
    return {"items": HistoricalReplayQueue(SessionLocal).inspect_dates(
        frozenset({principal.active_tenant_id}), parsed,
    )}


@router.get("/gemini/replay/jobs")
def active_historical_gemini_jobs(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    return {"items": HistoricalReplayQueue(SessionLocal).active_jobs(principal.active_tenant_id)}


@router.get("/gemini/replay/jobs/{job_id}")
def historical_gemini_job_status(
    job_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return HistoricalReplayQueue(SessionLocal).status(
            principal.active_tenant_id, job_id,
        )
    except LookupError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc


@router.post("/lifecycle-history/{business_date}/gemini/replay")
def replay_historical_gemini(
    business_date: date,
    body: HistoricalReplayRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CONTROL_PERMISSION)),
):
    service = _service()
    try:
        if not service.is_agent_v4_configured(principal.active_tenant_id):
            raise HTTPException(
                409, detail={"code": "gemini_tool_sheet_agent_not_configured"}
            )
        result = service.replay_agent_v4_historical(
            principal.active_tenant_id,
            business_date,
            mode=body.mode,
            promote=body.promote,
        )
        if result.get("promoted"):
            try:
                result["morning_reset_recovery"] = (
                    InventoryDailyScheduler(SessionLocal)
                    .recover_current_morning_reset_after_replay(
                        principal.active_tenant_id,
                        business_date,
                    )
                )
            except Exception as recovery_exc:
                result["morning_reset_recovery"] = {
                    "status": "recovery_failed",
                    "stage": "morning_reset",
                    "error_code": str(
                        getattr(recovery_exc, "code", type(recovery_exc).__name__)
                    )[:100],
                }
        return result
    except HTTPException:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        metadata = inventory_error_metadata(code)
        raise HTTPException(
            409,
            detail={
                "code": code,
                "message": str(exc),
                "error_category": metadata["category"],
                "retryable": metadata["retryable"],
            },
        ) from exc


@router.post("/reconcile/run")
def run_reconcile(body: RunRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().reconcile(principal.active_tenant_id, _business_date(principal.active_tenant_id, body.business_date), dry_run=body.dry_run)
    except Exception as exc: raise HTTPException(409, detail={"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}) from exc

@router.post("/baseline")
def set_baseline(body: BaselineRequest, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION))):
    try: return _service().set_baseline(principal.active_tenant_id, body.snapshot_id)
    except LookupError as exc: raise HTTPException(404, detail={"code": str(exc)}) from exc

# Separate tenant-scoped manager approval API; approvals never apply inventory writes.
router.include_router(catinat_approval_router)
router.include_router(catinat_certification_router)
router.include_router(catinat_count_router)
router.include_router(catinat_transfer_router)