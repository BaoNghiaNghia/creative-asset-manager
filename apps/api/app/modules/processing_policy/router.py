from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
from app.modules.processing_policy.repository import ProcessingPolicyRepository, policy_document
from app.modules.processing_policy.schema import PauseRequest, ProviderPolicyPatch, ResumeRequest, TenantPolicyPatch
from app.modules.processing_policy.service import ProcessingPolicyService, TenantPolicyCache

router = APIRouter(prefix="/api/v1/admin/processing-policies", tags=["processing-policies"])
_cache: TenantPolicyCache | None = None

def _cache() -> TenantPolicyCache:
    global _cache
    settings = get_settings()
    if _cache is None or _cache.ttl_seconds != settings.PROCESSING_POLICY_CACHE_TTL_SECONDS:
        _cache = TenantPolicyCache(settings.PROCESSING_POLICY_CACHE_TTL_SECONDS)
    return _cache

def _response(service: ProcessingPolicyService, repository: ProcessingPolicyRepository, tenant_id: str) -> dict:
    effective = service.effective(tenant_id)
    return {
        "tenant_id": tenant_id,
        "configured": effective.configured,
        "effective": effective.effective,
        "global_upper_bounds": effective.global_upper_bounds,
        "providers": [policy_document(item) for item in repository.list_providers(tenant_id)],
        "job_counts": repository.operational_job_counts(tenant_id),
    }

@router.get("/{tenant_id}")
def get_policy(tenant_id: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.patch("/{tenant_id}")
def update_policy(tenant_id: str, body: TenantPolicyPatch, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    changes = body.model_dump(exclude_none=True, exclude={"reason"})
    if not changes:
        raise HTTPException(status_code=422, detail="At least one policy field is required")
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.update(tenant_id, changes, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.post("/{tenant_id}/pause")
def pause_tenant(tenant_id: str, body: PauseRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.pause(tenant_id, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        result["drain_mode"] = "graceful" if body.graceful_drain else "new_claims_stopped"
        session.commit()
        return result

@router.post("/{tenant_id}/resume")
def resume_tenant(tenant_id: str, body: ResumeRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.resume(tenant_id, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.patch("/{tenant_id}/providers/{provider_scope}/{provider_key}")
def update_provider(tenant_id: str, provider_scope: str, provider_key: str, body: ProviderPolicyPatch, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    changes = body.model_dump(exclude_none=True, exclude={"reason"})
    if not changes:
        raise HTTPException(status_code=422, detail="At least one provider policy field is required")
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.update_provider(tenant_id, provider_key, provider_scope, changes, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.post("/{tenant_id}/providers/{provider_scope}/{provider_key}/pause")
def pause_provider(tenant_id: str, provider_scope: str, provider_key: str, body: PauseRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.set_provider_pause(tenant_id, provider_key, provider_scope, paused=True, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.post("/{tenant_id}/providers/{provider_scope}/{provider_key}/resume")
def resume_provider(tenant_id: str, provider_scope: str, provider_key: str, body: ResumeRequest, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        repository = ProcessingPolicyRepository(session)
        service = ProcessingPolicyService(repository, get_settings(), _cache())
        service.set_provider_pause(tenant_id, provider_key, provider_scope, paused=False, actor_id=admin.actor_id, reason=body.reason)
        result = _response(service, repository, tenant_id)
        session.commit()
        return result

@router.get("/{tenant_id}/jobs/counts")
def job_counts(tenant_id: str, admin: ProcessingAdmin = Depends(require_processing_admin)):
    admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        return ProcessingPolicyRepository(session).operational_job_counts(tenant_id)
