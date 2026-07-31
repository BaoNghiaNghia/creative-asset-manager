from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_operations.control_schema import (
    AiBudgetUpdate, AiBulkJobRetry, AiConfigurationUpdate, AiDefaultsUpdate, AiJobMutation, AiPauseRequest,
    AiProviderControlUpdate,
)
from app.modules.ai_operations.controls import (
    AiOperationsControlError, AiOperationsControlService,
)
from app.modules.authorization.principal import CurrentPrincipal, require_permission, require_tenant_scope
from app.modules.processing_policy.service import TenantPolicyCache
from app.providers.ai.factory import build_ai_provider_registry


router = APIRouter(prefix="/api/v1/admin/ai-operations", tags=["ai-operations-controls"])
AI_OPERATIONS_READ = require_permission("ai_operations.read")
AI_PROVIDER_CONFIGURE = require_permission("ai_provider.configure")
AI_BUDGET_UPDATE = require_permission("ai_budget.update")
AI_EMERGENCY_STOP = require_permission("ai_emergency_stop")
AI_JOBS_RETRY = require_permission("ai_jobs.retry")
AI_JOBS_CANCEL = require_permission("ai_jobs.cancel")
_policy_cache: TenantPolicyCache | None = None


def _cache() -> TenantPolicyCache:
    global _policy_cache
    ttl = get_settings().PROCESSING_POLICY_CACHE_TTL_SECONDS
    if _policy_cache is None or _policy_cache.ttl_seconds != ttl:
        _policy_cache = TenantPolicyCache(ttl)
    return _policy_cache


def _tenant(principal: CurrentPrincipal, tenant_id: str | None) -> str:
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    return target


def _error(exc: AiOperationsControlError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


async def _mutate(tenant_id: str, operation):
    settings = get_settings()
    registry = build_ai_provider_registry(settings)
    try:
        with SessionLocal() as session:
            service = AiOperationsControlService(
                session, settings, registry, _cache()
            )
            try:
                result = operation(service)
                session.commit()
                return result
            except AiOperationsControlError as exc:
                session.rollback()
                raise _error(exc) from exc
    finally:
        await registry.aclose()


def _audit(principal: CurrentPrincipal, action: str, reason: str) -> dict:
    return {
        "actor": principal.user_id,
        "action": action,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/configuration")
async def read_ai_configuration(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    target = _tenant(principal, tenant_id)
    document = await _mutate(
        target,
        lambda service: service.configuration(
            target, platform_admin=principal.platform_admin
        ),
    )
    allowed = lambda permission: (
        principal.platform_admin or permission in principal.effective_permissions
    )
    document["permissions"] = {
        "can_manage_tenant": allowed("ai_provider.configure"),
        "can_configure_provider": allowed("ai_provider.configure"),
        "can_read_budget": allowed("ai_budget.read"),
        "can_update_budget": allowed("ai_budget.update"),
        "can_emergency_stop": allowed("ai_emergency_stop"),
        "can_retry_jobs": allowed("ai_jobs.retry"),
        "can_cancel_jobs": allowed("ai_jobs.cancel"),
        "can_manage_global": principal.platform_admin,
        "platform_admin": principal.platform_admin,
    }
    if not allowed("ai_budget.read"):
        document["budget"] = None
    return document


@router.patch("/configuration")
async def update_ai_configuration(
    body: AiConfigurationUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    policy = await _mutate(
        target,
        lambda service: service.update_configuration(
            target, changes, actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {
        "tenant_id": target,
        "policy": policy,
        "audit": _audit(principal, "ai_configuration_updated", body.reason),
    }

@router.post("/controls/pause")
async def pause_all_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.pause_all(
            target, actor_id=principal.user_id, reason=body.reason
        ),
    )
    return {"tenant_id": target, "state": "paused", "policy": policy, "audit": _audit(principal, "ai_paused", body.reason)}


@router.post("/controls/resume")
async def resume_all_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.resume_all(
            target, actor_id=principal.user_id, reason=body.reason
        ),
    )
    return {"tenant_id": target, "state": "resumed", "policy": policy, "audit": _audit(principal, "ai_resumed", body.reason)}


@router.patch("/controls/defaults")
async def update_ai_defaults(
    body: AiDefaultsUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.update_defaults(
            target, provider=body.provider, model=body.model,
            actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "policy": policy, "audit": _audit(principal, "ai_defaults_updated", body.reason)}


@router.post("/providers/{provider}/pause")
async def pause_ai_provider(
    provider: str,
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_provider_pause(
            target, provider, paused=True,
            actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "state": "paused", "policy": policy, "audit": _audit(principal, "ai_provider_paused", body.reason)}


@router.post("/providers/{provider}/resume")
async def resume_ai_provider(
    provider: str,
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_provider_pause(
            target, provider, paused=False,
            actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "state": "resumed", "policy": policy, "audit": _audit(principal, "ai_provider_resumed", body.reason)}


@router.patch("/providers/{provider}")
async def update_ai_provider_controls(
    provider: str,
    body: AiProviderControlUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    policy = await _mutate(
        target,
        lambda service: service.update_provider_controls(
            target, provider, changes,
            actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "policy": policy, "audit": _audit(principal, "ai_provider_updated", body.reason)}


@router.patch("/budget")
async def update_ai_budget(
    body: AiBudgetUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_BUDGET_UPDATE),
):
    target = _tenant(principal, tenant_id)
    changes = body.model_dump(exclude={"reason"})
    budget = await _mutate(
        target,
        lambda service: service.update_budget(
            target, changes, actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "budget": budget, "audit": _audit(principal, "ai_budget_updated", body.reason)}


@router.post("/jobs/retry-by-error")
async def retry_ai_jobs_by_error(
    body: AiBulkJobRetry,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_JOBS_RETRY),
):
    target = _tenant(principal, tenant_id)
    result = await _mutate(
        target,
        lambda service: service.retry_jobs_by_error_code(
            target, body.error_code, actor_id=principal.user_id,
            reason=body.reason, limit=body.limit,
        ),
    )
    return {
        "tenant_id": target,
        **result,
        "audit": _audit(principal, "ai_jobs_group_retry_requested", body.reason),
    }


@router.post("/jobs/{job_id}/retry")
async def retry_ai_job(
    job_id: str,
    body: AiJobMutation,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_JOBS_RETRY),
):
    target = _tenant(principal, tenant_id)
    job, outcome = await _mutate(
        target,
        lambda service: service.retry_job(
            target, job_id, actor_id=principal.user_id, reason=body.reason,
            force=body.force,
        ),
    )
    return {"tenant_id": target, "outcome": outcome, "job": job}


@router.post("/jobs/{job_id}/cancel")
async def cancel_ai_job(
    job_id: str,
    body: AiJobMutation,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_JOBS_CANCEL),
):
    target = _tenant(principal, tenant_id)
    job, outcome = await _mutate(
        target,
        lambda service: service.cancel_job(
            target, job_id, actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "outcome": outcome, "job": job}
