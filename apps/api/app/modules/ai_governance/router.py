from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.model import AiCostRateModel
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_governance.schema import BudgetOverrideRequest, BudgetPolicyPatch, CostRateCreate, RuntimeStopRequest
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.authorization.principal import (
    CurrentPrincipal, require_permission, require_platform_admin, require_tenant_scope,
)

router = APIRouter(prefix="/api/v1/admin/ai-governance", tags=["ai-governance"])
AI_OPERATIONS_READ = require_permission("ai_operations.read")
AI_BUDGET_READ = require_permission("ai_budget.read")
AI_BUDGET_UPDATE = require_permission("ai_budget.update")

def _policy_document(repo, tenant_id):
    policy = repo.get_policy(tenant_id)
    return {
        "tenant_id": tenant_id,
        "global_emergency_stop": get_settings().AI_EMERGENCY_STOP_ENABLED,
        "effective_ai_enabled": not get_settings().AI_EMERGENCY_STOP_ENABLED,
        "policy": None if policy is None else {
            "enabled": policy.enabled,
            "daily_limit_micros": policy.daily_limit_micros,
            "monthly_limit_micros": policy.monthly_limit_micros,
            "per_run_limit_micros": policy.per_run_limit_micros,
            "warning_threshold_percent": policy.warning_threshold_percent,
            "hard_stop_threshold_percent": policy.hard_stop_threshold_percent,
            "timezone": policy.timezone,
            "currency": policy.currency,
            "action_on_limit": policy.action_on_limit,
            "updated_at": policy.updated_at.isoformat(),
        },
    }

@router.get("/{tenant_id}/budget")
def get_budget(tenant_id: str, principal: CurrentPrincipal = Depends(AI_BUDGET_READ)):
    require_tenant_scope(principal, tenant_id)
    with SessionLocal() as session:
        return _policy_document(AiGovernanceRepository(session), tenant_id)

@router.patch("/{tenant_id}/budget")
def update_budget(tenant_id: str, body: BudgetPolicyPatch, principal: CurrentPrincipal = Depends(AI_BUDGET_UPDATE)):
    require_tenant_scope(principal, tenant_id)
    values = body.model_dump(exclude_unset=True, exclude={"reason"})
    values = {
        key: value for key, value in values.items()
        if value is not None or key in {"daily_limit_micros", "monthly_limit_micros", "per_run_limit_micros"}
    }
    if not values:
        raise HTTPException(status_code=422, detail="At least one budget field is required")
    with SessionLocal() as session:
        repo = AiGovernanceRepository(session)
        old = _policy_document(repo, tenant_id)["policy"]
        repo.upsert_policy(tenant_id, values)
        new = _policy_document(repo, tenant_id)["policy"]
        repo.event(tenant_id, "budget_policy_updated", actor_id=principal.user_id,
                   reason=body.reason, details={"old_policy": old, "new_policy": new})
        session.commit()
        return _policy_document(repo, tenant_id)

@router.post("/cost-rates")
def create_cost_rate(body: CostRateCreate, principal: CurrentPrincipal = Depends(require_platform_admin)):
    if not principal.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    try:
        effective_at = datetime.fromisoformat(body.effective_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid effective_at") from exc
    with SessionLocal() as session:
        value = AiCostRateModel(effective_at=effective_at, **body.model_dump(exclude={"effective_at"}))
        session.add(value)
        AiGovernanceRepository(session).event("platform", "cost_rate_changed", actor_id=principal.user_id, details={"provider": body.provider, "model": body.model, "processing_mode": body.processing_mode, "effective_at": body.effective_at})
        session.commit()
        return {"id": value.id, "provider": value.provider, "model": value.model,
                "effective_at": value.effective_at, "currency": value.currency, "processing_mode": value.processing_mode}

@router.get("/metrics")
def metrics(principal: CurrentPrincipal = Depends(require_platform_admin)):
    return AI_METRICS.snapshot()

@router.put("/runtime-controls/{control_key}")
def set_runtime_control(control_key: str, body: RuntimeStopRequest,
                        principal: CurrentPrincipal = Depends(require_platform_admin)):
    if not principal.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    with SessionLocal() as session:
        repo = AiGovernanceRepository(session)
        try:
            value = repo.set_runtime_stop(control_key, body.stopped, principal.user_id, body.reason)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
        return {"control_key": value.control_key, "stopped": value.stopped,
                "reason": value.reason, "updated_at": value.updated_at}

@router.post("/{tenant_id}/budget-overrides")
def grant_budget_override(tenant_id: str, body: BudgetOverrideRequest,
                          principal: CurrentPrincipal = Depends(require_platform_admin)):
    require_tenant_scope(principal, tenant_id)
    if not principal.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    with SessionLocal() as session:
        analysis = AiMetadataRepository(session).get_analysis(body.analysis_id)
        if analysis.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Analysis not found")
        value = AiGovernanceRepository(session).grant_budget_override(
            tenant_id, body.analysis_id, principal.user_id, body.reason)
        session.commit()
        return {"id": value.id, "analysis_id": value.analysis_id, "active": value.active}
