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
from app.providers.ai.gemini import probe_gemini_api_key, validate_gemini_api_key
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository, CreativeCredentialError, CreativeGeminiCredentialResolver, creative_credential_cipher, gemini_backup_provider, gemini_backup_providers, gemini_video_backup_provider, gemini_video_backup_providers
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



def _delete_gemini_credential(
    target: str, provider: str, principal: CurrentPrincipal
) -> dict:
    try:
        with SessionLocal() as session:
            repository = CreativeAiCredentialRepository(
                session, creative_credential_cipher(get_settings())
            )
            removed = repository.remove(target, provider=provider)
            if removed is not None:
                repository.audit(
                    target, provider=provider, actor_id=principal.user_id,
                    action="credential_deleted", result="DELETED",
                    previous_fingerprint=removed.secret_fingerprint,
                )
            session.commit()
    except (CreativeCredentialError, SQLAlchemyError) as exc:
        raise _creative_credential_error(exc) from exc
    return {"provider": provider, "deleted": removed is not None}


def _gemini_test_result(provider: str, api_key: str | None) -> dict:
    """Return a safe Gemini test result without exposing response bodies or keys."""
    if not api_key:
        return {"provider": provider, "status": "PROVIDER_UNAVAILABLE", "http_status": None}
    probe = probe_gemini_api_key(
        api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10)
    )
    return {
        "provider": provider,
        "status": probe.status,
        "http_status": probe.http_status,
    }


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
            api_key = None
    else:
        api_key = body.api_key
    response = _gemini_test_result("gemini", api_key)
    _CREDENTIAL_LOGGER.info(
        "creative_gemini_credential_test tenant_id=%s actor_id=%s provider=gemini result=%s http_status=%s",
        target, principal.user_id, response["status"], response["http_status"],
    )
    return response
@router.delete("/configuration/credentials/gemini")
def delete_creative_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(
        _tenant(principal, tenant_id), "gemini", principal
    )


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


@router.get("/configuration/credentials/gemini-video")
def get_video_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    target = _tenant(principal, tenant_id)
    try:
        with SessionLocal() as session:
            metadata = CreativeAiCredentialRepository(
                session, None
            ).get_metadata(target, provider="gemini_video")
    except SQLAlchemyError as exc:
        raise _creative_credential_error(exc) from exc
    result = _creative_credential_view(metadata, source="configuration" if metadata else "unavailable")
    result["provider"] = "gemini_video"
    return result


@router.post("/configuration/credentials/gemini-video/test")
def test_video_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    if body.api_key is not None:
        api_key = body.api_key
    else:
        try:
            with SessionLocal() as session:
                credential = CreativeAiCredentialRepository(
                    session, creative_credential_cipher(get_settings())
                ).get_active_secret(target, provider="gemini_video")
            api_key = credential.secret if credential else None
        except CreativeCredentialError:
            api_key = None
    return _gemini_test_result("gemini_video", api_key)
@router.delete("/configuration/credentials/gemini-video")
def delete_video_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(
        _tenant(principal, tenant_id), "gemini_video", principal
    )


@router.put("/configuration/credentials/gemini-video")
def replace_video_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    if body.api_key is None:
        raise HTTPException(422, detail={"code": "video_gemini_credential_required"})
    result = validate_gemini_api_key(body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10))
    if result != "VALID":
        raise HTTPException(422, detail={"code": "video_gemini_credential_invalid", "status": result})
    target = _tenant(principal, tenant_id)
    try:
        with SessionLocal() as session:
            repository = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings()))
            metadata = repository.replace(
                target, secret=body.api_key, provider="gemini_video", label=body.label,
                updated_by=principal.user_id,
            )
            repository.audit(
                target, provider="gemini_video", actor_id=principal.user_id,
                action="credential_replaced", result="VALID",
                new_fingerprint=metadata.secret_fingerprint,
            )
            session.commit()
    except (CreativeCredentialError, SQLAlchemyError) as exc:
        raise _creative_credential_error(exc) from exc
    result = _creative_credential_view(metadata, source="configuration")
    result["provider"] = "gemini_video"
    return result



@router.get("/configuration/credentials/gemini-backup")
def get_backup_gemini_credential(tenant_id: str | None = Query(default=None), principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ)):
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        metadata = CreativeAiCredentialRepository(session, None).get_metadata(target, provider=gemini_backup_provider(1))
    result = _creative_credential_view(metadata, source="configuration" if metadata else "unavailable")
    result["provider"] = "gemini_backup"
    return result

@router.post("/configuration/credentials/gemini-backup/test")
def test_backup_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    if body.api_key is not None:
        api_key = body.api_key
    else:
        try:
            with SessionLocal() as session:
                credential = CreativeAiCredentialRepository(
                    session, creative_credential_cipher(get_settings())
                ).get_active_secret(target, provider=gemini_backup_provider(1))
            api_key = credential.secret if credential else None
        except CreativeCredentialError:
            api_key = None
    return _gemini_test_result("gemini_backup", api_key)
@router.delete("/configuration/credentials/gemini-backup")
def delete_backup_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(
        _tenant(principal, tenant_id), gemini_backup_provider(1), principal
    )


@router.put("/configuration/credentials/gemini-backup")
def replace_backup_gemini_credential(body: CreativeGeminiCredentialRequest, tenant_id: str | None = Query(default=None), principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE)):
    if body.api_key is None: raise HTTPException(422, detail={"code": "gemini_backup_credential_required"})
    result = validate_gemini_api_key(body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10))
    if result != "VALID": raise HTTPException(422, detail={"code": "gemini_backup_credential_invalid", "status": result})
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        repository = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings()))
        metadata = repository.replace(target, secret=body.api_key, provider=gemini_backup_provider(1), label=body.label, updated_by=principal.user_id)
        repository.audit(target, provider=gemini_backup_provider(1), actor_id=principal.user_id, action="credential_replaced", result="VALID", new_fingerprint=metadata.secret_fingerprint)
        session.commit()
    result = _creative_credential_view(metadata, source="configuration"); result["provider"] = "gemini_backup"
    return result


@router.get("/configuration/credentials/gemini-backup-2")
def get_backup_2_gemini_credential(tenant_id: str | None = Query(default=None), principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ)):
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        metadata = CreativeAiCredentialRepository(session, None).get_metadata(target, provider="gemini_backup_2")
    result = _creative_credential_view(metadata, source="configuration" if metadata else "unavailable")
    result["provider"] = "gemini_backup_2"
    return result

@router.post("/configuration/credentials/gemini-backup-2/test")
def test_backup_2_gemini_credential(
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    if body.api_key is not None:
        api_key = body.api_key
    else:
        try:
            with SessionLocal() as session:
                credential = CreativeAiCredentialRepository(
                    session, creative_credential_cipher(get_settings())
                ).get_active_secret(target, provider="gemini_backup_2")
            api_key = credential.secret if credential else None
        except CreativeCredentialError:
            api_key = None
    return _gemini_test_result("gemini_backup_2", api_key)
@router.delete("/configuration/credentials/gemini-backup-2")
def delete_backup_2_gemini_credential(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(
        _tenant(principal, tenant_id), "gemini_backup_2", principal
    )


@router.put("/configuration/credentials/gemini-backup-2")
def replace_backup_2_gemini_credential(body: CreativeGeminiCredentialRequest, tenant_id: str | None = Query(default=None), principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE)):
    if body.api_key is None: raise HTTPException(422, detail={"code": "gemini_backup_2_credential_required"})
    result = validate_gemini_api_key(body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10))
    if result != "VALID": raise HTTPException(422, detail={"code": "gemini_backup_2_credential_invalid", "status": result})
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        repository = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings()))
        metadata = repository.replace(target, secret=body.api_key, provider="gemini_backup_2", label=body.label, updated_by=principal.user_id)
        repository.audit(target, provider="gemini_backup_2", actor_id=principal.user_id, action="credential_replaced", result="VALID", new_fingerprint=metadata.secret_fingerprint)
        session.commit()
    result = _creative_credential_view(metadata, source="configuration"); result["provider"] = "gemini_backup_2"
    return result


def _backup_slot_provider(slot: int) -> str:
    try:
        return gemini_backup_provider(slot)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "gemini_backup_slot_invalid"}) from exc


@router.get("/configuration/credentials/gemini-backups")
def list_backup_gemini_credentials(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        items = {item.provider: item for item in CreativeAiCredentialRepository(session, None).list_backup_metadata(target)}
    return [
        {**_creative_credential_view(items.get(provider), source="configuration" if provider in items else "unavailable"), "provider": provider, "slot": slot}
        for slot, provider in enumerate(gemini_backup_providers(), start=1)
    ]


@router.post("/configuration/credentials/gemini-backups/{slot}/test")
def test_dynamic_backup_gemini_credential(
    slot: int, body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    provider, target = _backup_slot_provider(slot), _tenant(principal, tenant_id)
    api_key = body.api_key
    if api_key is None:
        try:
            with SessionLocal() as session:
                credential = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings())).get_active_secret(target, provider=provider)
            api_key = credential.secret if credential else None
        except CreativeCredentialError:
            api_key = None
    return _gemini_test_result(provider, api_key)


@router.put("/configuration/credentials/gemini-backups/{slot}")
def replace_dynamic_backup_gemini_credential(
    slot: int, body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    provider = _backup_slot_provider(slot)
    if body.api_key is None:
        raise HTTPException(422, detail={"code": "gemini_backup_credential_required"})
    result = validate_gemini_api_key(body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10))
    if result != "VALID":
        raise HTTPException(422, detail={"code": "gemini_backup_credential_invalid", "status": result})
    target = _tenant(principal, tenant_id)
    try:
        with SessionLocal() as session:
            repo = CreativeAiCredentialRepository(session, creative_credential_cipher(get_settings()))
            metadata = repo.replace(target, secret=body.api_key, provider=provider, label=body.label, updated_by=principal.user_id)
            repo.audit(target, provider=provider, actor_id=principal.user_id, action="credential_replaced", result="VALID", new_fingerprint=metadata.secret_fingerprint)
            session.commit()
    except (CreativeCredentialError, SQLAlchemyError) as exc:
        raise _creative_credential_error(exc) from exc
    return {**_creative_credential_view(metadata, source="configuration"), "provider": provider, "slot": slot}


@router.delete("/configuration/credentials/gemini-backups/{slot}")
def delete_dynamic_backup_gemini_credential(
    slot: int, tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(_tenant(principal, tenant_id), _backup_slot_provider(slot), principal)

def _video_backup_slot_provider(slot: int) -> str:
    try:
        return gemini_video_backup_provider(slot)
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "gemini_video_backup_slot_invalid"}
        ) from exc


@router.get("/configuration/credentials/gemini-video-backups")
def list_video_backup_gemini_credentials(
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    target = _tenant(principal, tenant_id)
    with SessionLocal() as session:
        items = {
            item.provider: item
            for item in CreativeAiCredentialRepository(
                session, None
            ).list_video_backup_metadata(target)
        }
    return [
        {
            **_creative_credential_view(
                items.get(provider),
                source="configuration" if provider in items else "unavailable",
            ),
            "provider": provider,
            "slot": slot,
        }
        for slot, provider in enumerate(gemini_video_backup_providers(), start=1)
    ]


@router.post("/configuration/credentials/gemini-video-backups/{slot}/test")
def test_video_backup_gemini_credential(
    slot: int,
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    provider = _video_backup_slot_provider(slot)
    target = _tenant(principal, tenant_id)
    api_key = body.api_key
    if api_key is None:
        try:
            with SessionLocal() as session:
                credential = CreativeAiCredentialRepository(
                    session, creative_credential_cipher(get_settings())
                ).get_active_secret(target, provider=provider)
            api_key = credential.secret if credential else None
        except CreativeCredentialError:
            api_key = None
    return _gemini_test_result(provider, api_key)


@router.put("/configuration/credentials/gemini-video-backups/{slot}")
def replace_video_backup_gemini_credential(
    slot: int,
    body: CreativeGeminiCredentialRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    provider = _video_backup_slot_provider(slot)
    if body.api_key is None:
        raise HTTPException(
            422, detail={"code": "gemini_video_backup_credential_required"}
        )
    result = validate_gemini_api_key(
        body.api_key, timeout_seconds=min(get_settings().GEMINI_TIMEOUT_SECONDS, 10)
    )
    if result != "VALID":
        raise HTTPException(
            422,
            detail={
                "code": "gemini_video_backup_credential_invalid",
                "status": result,
            },
        )
    target = _tenant(principal, tenant_id)
    try:
        with SessionLocal() as session:
            repo = CreativeAiCredentialRepository(
                session, creative_credential_cipher(get_settings())
            )
            metadata = repo.replace(
                target,
                secret=body.api_key,
                provider=provider,
                label=body.label,
                updated_by=principal.user_id,
            )
            repo.audit(
                target,
                provider=provider,
                actor_id=principal.user_id,
                action="credential_replaced",
                result="VALID",
                new_fingerprint=metadata.secret_fingerprint,
            )
            session.commit()
    except (CreativeCredentialError, SQLAlchemyError) as exc:
        raise _creative_credential_error(exc) from exc
    return {
        **_creative_credential_view(metadata, source="configuration"),
        "provider": provider,
        "slot": slot,
    }


@router.delete("/configuration/credentials/gemini-video-backups/{slot}")
def delete_video_backup_gemini_credential(
    slot: int,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    return _delete_gemini_credential(
        _tenant(principal, tenant_id),
        _video_backup_slot_provider(slot),
        principal,
    )


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


@router.patch("/configuration/video-prompt-template")
async def update_video_prompt_template(
    body: AiMetadataPromptTemplateUpdate,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(AI_PROVIDER_CONFIGURE),
):
    target = _tenant(principal, tenant_id)
    profile = await _mutate(
        target,
        lambda service: service.update_video_prompt_template(
            target,
            prompt_template=body.prompt_template,
            actor_id=principal.user_id,
            reason=body.reason,
        ),
    )
    return {
        "tenant_id": target,
        "video_prompt_template": profile,
        "audit": _audit(principal, "video_prompt_template_updated", body.reason),
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
            reason=body.reason, limit=body.limit, job_type=body.job_type,
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
