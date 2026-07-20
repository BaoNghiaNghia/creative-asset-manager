from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.model import AiCostRateModel
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_governance.schema import BudgetPolicyPatch, CostRateCreate
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin

router = APIRouter(prefix="/api/v1/admin/ai-governance", tags=["ai-governance"])

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
def get_budget(tenant_id: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        return _policy_document(AiGovernanceRepository(session), tenant_id)

@router.patch("/{tenant_id}/budget")
def update_budget(tenant_id: str, body: BudgetPolicyPatch, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
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
        repo.event(tenant_id, "budget_policy_updated", actor_id=admin.actor_id,
                   reason=body.reason, details={"old_policy": old, "new_policy": new})
        session.commit()
        return _policy_document(repo, tenant_id)

@router.post("/cost-rates")
def create_cost_rate(body: CostRateCreate, admin: ProcessingAdmin = Depends(require_processing_admin)):
    if not admin.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator role required")
    try:
        effective_at = datetime.fromisoformat(body.effective_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid effective_at") from exc
    with SessionLocal() as session:
        value = AiCostRateModel(effective_at=effective_at, **body.model_dump(exclude={"effective_at"}))
        session.add(value)
        session.commit()
        return {"id": value.id, "provider": value.provider, "model": value.model,
                "effective_at": value.effective_at, "currency": value.currency}

@router.get("/metrics")
def metrics(admin: ProcessingAdmin = Depends(require_processing_admin)):
    return AI_METRICS.snapshot()
