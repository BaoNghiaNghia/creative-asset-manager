from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_operations.control_schema import (
    AiBudgetUpdate, AiDefaultsUpdate, AiJobMutation, AiPauseRequest,
    AiProviderControlUpdate,
)
from app.modules.ai_operations.controls import (
    AiOperationsControlError, AiOperationsControlService,
)
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
from app.modules.processing_policy.service import TenantPolicyCache
from app.providers.ai.factory import build_ai_provider_registry


router = APIRouter(prefix="/api/v1/admin/ai-operations", tags=["ai-operations-controls"])
_policy_cache: TenantPolicyCache | None = None


def _cache() -> TenantPolicyCache:
    global _policy_cache
    ttl = get_settings().PROCESSING_POLICY_CACHE_TTL_SECONDS
    if _policy_cache is None or _policy_cache.ttl_seconds != ttl:
        _policy_cache = TenantPolicyCache(ttl)
    return _policy_cache


def _tenant(admin: ProcessingAdmin, tenant_id: str | None) -> str:
    target = tenant_id or admin.own_tenant_id
    admin.authorize_tenant(target)
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


@router.post("/controls/pause")
async def pause_all_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.pause_all(
            target, actor_id=admin.actor_id, reason=body.reason
        ),
    )
    return {"tenant_id": target, "state": "paused", "policy": policy}


@router.post("/controls/resume")
async def resume_all_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.resume_all(
            target, actor_id=admin.actor_id, reason=body.reason
        ),
    )
    return {"tenant_id": target, "state": "resumed", "policy": policy}


@router.patch("/controls/defaults")
async def update_ai_defaults(
    body: AiDefaultsUpdate,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.update_defaults(
            target, provider=body.provider, model=body.model,
            actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "policy": policy}


@router.post("/providers/{provider}/pause")
async def pause_ai_provider(
    provider: str,
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_provider_pause(
            target, provider, paused=True,
            actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "state": "paused", "policy": policy}


@router.post("/providers/{provider}/resume")
async def resume_ai_provider(
    provider: str,
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_provider_pause(
            target, provider, paused=False,
            actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "state": "resumed", "policy": policy}


@router.patch("/providers/{provider}")
async def update_ai_provider_controls(
    provider: str,
    body: AiProviderControlUpdate,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    changes = body.model_dump(exclude_none=True, exclude={"reason"})
    policy = await _mutate(
        target,
        lambda service: service.update_provider_controls(
            target, provider, changes,
            actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "provider": provider, "policy": policy}


@router.patch("/budget")
async def update_ai_budget(
    body: AiBudgetUpdate,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    changes = body.model_dump(exclude={"reason"})
    budget = await _mutate(
        target,
        lambda service: service.update_budget(
            target, changes, actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "budget": budget}


@router.post("/jobs/{job_id}/retry")
async def retry_ai_job(
    job_id: str,
    body: AiJobMutation,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    job, outcome = await _mutate(
        target,
        lambda service: service.retry_job(
            target, job_id, actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "outcome": outcome, "job": job}


@router.post("/jobs/{job_id}/cancel")
async def cancel_ai_job(
    job_id: str,
    body: AiJobMutation,
    tenant_id: str | None = Query(default=None),
    admin: ProcessingAdmin = Depends(require_processing_admin),
):
    target = _tenant(admin, tenant_id)
    job, outcome = await _mutate(
        target,
        lambda service: service.cancel_job(
            target, job_id, actor_id=admin.actor_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "outcome": outcome, "job": job}
