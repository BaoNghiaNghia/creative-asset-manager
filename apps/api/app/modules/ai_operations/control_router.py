from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_operations.control_schema import (
    AiBudgetUpdate, AiBulkJobRetry, AiConfigurationUpdate, AiDefaultsUpdate, AiJobMutation, AiPauseRequest,
    AiProviderControlUpdate, AiMetadataPromptTemplateUpdate, CreativeGeminiCredentialRequest,
)
from app.modules.ai_operations.controls import (
    AiOperationsControlError, AiOperationsControlService,
)
from app.modules.authorization.principal import CurrentPrincipal, require_permission, require_tenant_scope
from app.modules.processing_policy.service import TenantPolicyCache
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.ai.gemini import validate_gemini_api_key
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository, CreativeCredentialError, CreativeGeminiCredentialResolver, creative_credential_cipher
import logging

_CREDENTIAL_LOGGER = logging.getLogger("cam.creative_gemini_credential")


router = APIRouter(prefix="/api/v1/admin/ai-operations", tags=["ai-operations-controls"])
AI_OPERATIONS_READ = require_permission("ai_operations.read")
AI_PROVIDER_CONFIGURE = require_permission("ai_provider.configure")
AI_BUDGET_UPDATE = require_permission("ai_budget.update")
AI_EMERGENCY_STOP = require_permission("ai_emergency_stop")
AI_JOBS_RETRY = require_permission("ai_jobs.retry")
AI_JOBS_CANCEL = require_permission("ai_jobs.cancel")
_policy_cache: TenantPolicyCache | None = None



def _creative_credential_view(metadata, *, source: str) -> dict:
    if metadata is None:
        return {"provider": "gemini", "configured": False, "source": source, "masked_key": None, "label": None, "status": "unavailable", "last_tested_at": None, "updated_at": None, "updated_by": None}
    return {"provider": "gemini", "configured": True, "source": source, "masked_key": f"••••••••{metadata.secret_last4}", "label": metadata.label, "status": "connected" if metadata.status == "active" else metadata.status, "last_tested_at": metadata.last_tested_at, "updated_at": metadata.updated_at, "updated_by": metadata.updated_by}


def _creative_credential_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "creative_credential_storage_unavailable")
    if code in {"creative_credential_encryption_unavailable", "creative_ai_credential_decryption_failed"}:
        return HTTPException(503, detail={"code": code, "message": "Creative credential encryption is not configured correctly on the server."})
    return HTTPException(503, detail={"code": "creative_credential_storage_unavailable", "message": "Creative credential storage is not ready."})


@router.get("/configuration/credentials/gemini")
def get_creative_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    target = _tenant(principal, tenant_id)
    try:
        with SessionLocal() as session:
            metadata = CreativeAiCredentialRepository(session, None).get_metadata(target)
    except SQLAlchemyError as exc:
        raise _creative_credential_error(exc) from exc
    if metadata is not None:
        return _creative_credential_view(metadata, source="configuration")
    fallback = (get_settings().GEMINI_API_KEY or "").strip()
    if fallback:
        return {"provider": "gemini", "configured": True, "source": "environment", "masked_key": f"••••••••{fallback[-4:]}", "label": None, "status": "connected", "last_tested_at": None, "updated_at": None, "updated_by": None}
    return _creative_credential_view(None, source="unavailable")


@router.post("/configuration/credentials/gemini/test")
def test_creative_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    if body.api_key is None:
        try:
            api_key = CreativeGeminiCredentialResolver(
                SessionLocal, get_settings()
            ).resolve(target).secret
        except CreativeCredentialError:
            result = "PROVIDER_UNAVAILABLE"
        else:
            result = validate_gemini_api_key(
                api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10)
            )
    else:
        result = validate_gemini_api_key(
            body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10)
        )
    _CREDENTIAL_LOGGER.info("creative_gemini_credential_test tenant_id=%s actor_id=%s provider=gemini result=%s", target, principal.user_id, result)
    return {"provider": "gemini", "status": result}


@router.put("/configuration/credentials/gemini")
def replace_creative_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    if body.api_key is None:
        raise HTTPException(422, detail={"code": "creative_gemini_credential_required"})
    result = validate_gemini_api_key(body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10))
    try:
        with SessionLocal() as session:
            repository = CreativeAiCredentialRepository(session, None)
            previous = repository.get_metadata(target)
            if result != "VALID":
                repository.audit(target, actor_id=principal.user_id, action="credential_validation", result=result, previous_fingerprint=previous.secret_fingerprint if previous else None)
                session.commit()
                raise HTTPException(422, detail={"code": "creative_gemini_credential_invalid", "status": result})
            repository = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings()))
            metadata = repository.replace(target, secret=body.api_key, label=body.label, updated_by=principal.user_id, last_test_status="VALID")
            repository.audit(target, actor_id=principal.user_id, action="credential_replaced", result="VALID", previous_fingerprint=previous.secret_fingerprint if previous else None, new_fingerprint=metadata.secret_fingerprint)
            session.commit()
    except HTTPException:
        raise
    except (CreativeCredentialError, SQLAlchemyError) as exc:
        _CREDENTIAL_LOGGER.warning("creative_gemini_credential_replace tenant_id=%s actor_id=%s provider=gemini error_code=%s", target, principal.user_id, getattr(exc, "code", type(exc).__name__))
        raise _creative_credential_error(exc) from exc
    _CREDENTIAL_LOGGER.info("creative_gemini_credential_replace tenant_id=%s actor_id=%s provider=gemini result=VALID", target, principal.user_id)
    return _creative_credential_view(metadata, source="configuration")

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
    # AI Operations configuration/control requests must use the same
    # tenant-scoped Creative Gemini credential boundary as runtime workers.
    # Without SessionLocal the registry can only see environment credentials,
    # so a valid Gemini credential stored in the database is incorrectly
    # reported as "Connection not configured".
    registry = build_ai_provider_registry(
        settings,
        session_factory=SessionLocal,
    )
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


@router.patch("/configuration/metadata-prompt-template")
async def update_metadata_prompt_template(
    body: AiMetadataPromptTemplateUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    profile = await _mutate(
        target,
        lambda service: service.update_metadata_prompt_template(
            target,
            prompt_template=body.prompt_template,
            actor_id=principal.user_id,
            reason=body.reason,
        ),
    )
    return {
        "tenant_id": target,
        "metadata_prompt_template": profile,
        "audit": _audit(principal, "metadata_prompt_template_updated", body.reason),
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


@router.post("/controls/video/pause")
async def pause_video_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_video_pause(
            target, paused=True, actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "state": "paused", "policy": policy, "audit": _audit(principal, "video_ai_paused", body.reason)}


@router.post("/controls/video/resume")
async def resume_video_ai(
    body: AiPauseRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_EMERGENCY_STOP),
):
    target = _tenant(principal, tenant_id)
    policy = await _mutate(
        target,
        lambda service: service.set_video_pause(
            target, paused=False, actor_id=principal.user_id, reason=body.reason,
        ),
    )
    return {"tenant_id": target, "state": "resumed", "policy": policy, "audit": _audit(principal, "video_ai_resumed", body.reason)}


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
