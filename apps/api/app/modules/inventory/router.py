from datetime import date
import logging


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.providers.ai.gemini import validate_gemini_api_key as validate_gemini_candidate
from app.modules.inventory.daily.service import DailyRunBlocked, InventoryDailyRunService
from app.modules.inventory.daily.report import DailyReportNotFinalized, InventoryDailyReportService
from app.modules.inventory.exports.service import InventoryExportFailure, InventoryExportService
from app.modules.inventory.credentials import (
    InventoryAiCredentialRepository,
    InventoryCredentialError,
    InventoryGeminiCredentialResolver,
    inventory_credential_cipher,
)
from app.modules.inventory.permissions import (
    INVENTORY_CREDENTIALS_MANAGE_PERMISSION,
    INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_EXPORT_PERMISSION,
    INVENTORY_READ_PERMISSION,
    INVENTORY_REVIEW_PERMISSION,
)
from app.modules.inventory.review.service import InventoryReviewService

router = APIRouter(prefix="/api/inventory", tags=["inventory"])
_CREDENTIAL_LOGGER = logging.getLogger("cam.inventory.credentials_api")


class GeminiCredentialRequest(BaseModel):
    # An omitted key means test the credential currently resolved for this
    # tenant. A supplied key remains a non-persisted candidate test.
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=255)


def _credential_view(metadata, *, source: str) -> dict:
    if metadata is None:
        return {
            "provider": "gemini", "configured": False, "source": "unavailable",
            "masked_key": None, "label": None, "status": "unavailable",
            "last_tested_at": None, "updated_at": None, "updated_by": None,
        }
    return {
        "provider": metadata.provider, "configured": True, "source": source,
        "masked_key": f"••••••••{metadata.secret_last4}", "label": metadata.label,
        "status": "connected" if metadata.status == "active" else metadata.status,
        "last_tested_at": metadata.last_tested_at, "updated_at": metadata.updated_at,
        "updated_by": metadata.updated_by,
    }


def _credential_repository(session):
    return InventoryAiCredentialRepository(session, inventory_credential_cipher(get_settings()))


def _credential_metadata_repository(session):
    return InventoryAiCredentialRepository(session, None)


@router.get("/configuration/ai-credential")
def get_ai_credential(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    with SessionLocal() as session:
        metadata = _credential_metadata_repository(session).get_metadata(principal.active_tenant_id)
    if metadata is not None:
        return _credential_view(metadata, source="configuration")
    environment_key = get_settings().inventory_gemini_api_key
    if environment_key:
        # Environment credentials remain deployment-managed. Return only a masked
        # suffix so operators can confirm that the fallback is active safely.
        return {
            "provider": "gemini", "configured": True, "source": "environment",
            "masked_key": f"••••••••{environment_key[-4:]}", "label": None,
            "status": "connected", "last_tested_at": None, "updated_at": None,
            "updated_by": None,
        }
    return _credential_view(None, source="unavailable")


@router.post("/configuration/ai-credential/test")
def test_ai_credential(
    body: GeminiCredentialRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CREDENTIALS_MANAGE_PERMISSION)),
):
    if body.api_key is None:
        try:
            api_key = InventoryGeminiCredentialResolver(
                SessionLocal, get_settings()
            ).resolve(principal.active_tenant_id)
        except InventoryCredentialError:
            # A broken configured override must not be bypassed with another
            # credential while this endpoint is merely testing it.
            result = "PROVIDER_UNAVAILABLE"
        else:
            result = validate_gemini_candidate(api_key)
    else:
        result = validate_gemini_candidate(body.api_key)
    _CREDENTIAL_LOGGER.info(
        "inventory_gemini_credential_test tenant_id=%s actor_id=%s provider=gemini result=%s",
        principal.active_tenant_id, principal.actor_id, result,
    )
    return {"provider": "gemini", "status": result}


@router.put("/configuration/ai-credential")
def replace_ai_credential(
    body: GeminiCredentialRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_CREDENTIALS_MANAGE_PERMISSION)),
):
    if body.api_key is None:
        raise HTTPException(422, detail={"code": "inventory_gemini_credential_required"})
    result = validate_gemini_candidate(body.api_key)
    with SessionLocal() as session:
        try:
            # Metadata access intentionally does not construct the cipher.
            previous = _credential_metadata_repository(session).get_metadata(principal.active_tenant_id)
        except SQLAlchemyError as exc:
            session.rollback()
            _CREDENTIAL_LOGGER.warning(
                "inventory_gemini_credential_storage tenant_id=%s actor_id=%s provider=gemini error_class=%s",
                principal.active_tenant_id, principal.actor_id, type(exc).__name__,
            )
            raise HTTPException(
                503,
                detail={
                    "code": "inventory_credential_storage_unavailable",
                    "message": "Inventory credential storage is not ready.",
                },
            ) from exc

        if result != "VALID":
            try:
                _credential_metadata_repository(session).audit(
                    principal.active_tenant_id, actor_id=principal.actor_id,
                    action="credential_validation", result=result,
                    previous_fingerprint=previous.secret_fingerprint if previous else None,
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                _CREDENTIAL_LOGGER.warning(
                    "inventory_gemini_credential_storage tenant_id=%s actor_id=%s provider=gemini error_class=%s",
                    principal.active_tenant_id, principal.actor_id, type(exc).__name__,
                )
                raise HTTPException(
                    503,
                    detail={
                        "code": "inventory_credential_storage_unavailable",
                        "message": "Inventory credential storage is not ready.",
                    },
                ) from exc
            _CREDENTIAL_LOGGER.info(
                "inventory_gemini_credential_replace tenant_id=%s actor_id=%s provider=gemini result=%s",
                principal.active_tenant_id, principal.actor_id, result,
            )
            raise HTTPException(
                422,
                detail={"code": "inventory_gemini_credential_invalid", "status": result},
            )

        try:
            repository = _credential_repository(session)
            metadata = repository.replace(
                principal.active_tenant_id, secret=body.api_key, label=body.label,
                updated_by=principal.actor_id, last_test_status="VALID",
            )
            repository.audit(
                principal.active_tenant_id, actor_id=principal.actor_id,
                action="credential_replaced", result="VALID",
                previous_fingerprint=previous.secret_fingerprint if previous else None,
                new_fingerprint=metadata.secret_fingerprint,
            )
            session.commit()
        except InventoryCredentialError as exc:
            session.rollback()
            _CREDENTIAL_LOGGER.warning(
                "inventory_gemini_credential_replace tenant_id=%s actor_id=%s provider=gemini result=%s",
                principal.active_tenant_id, principal.actor_id, str(exc),
            )
            raise HTTPException(
                503,
                detail={
                    "code": "inventory_credential_encryption_unavailable",
                    "message": "Credential encryption is not configured correctly on the server.",
                },
            ) from exc
        except SQLAlchemyError as exc:
            session.rollback()
            _CREDENTIAL_LOGGER.warning(
                "inventory_gemini_credential_storage tenant_id=%s actor_id=%s provider=gemini error_class=%s",
                principal.active_tenant_id, principal.actor_id, type(exc).__name__,
            )
            raise HTTPException(
                503,
                detail={
                    "code": "inventory_credential_storage_unavailable",
                    "message": "Inventory credential storage is not ready.",
                },
            ) from exc
    _CREDENTIAL_LOGGER.info(
        "inventory_gemini_credential_replace tenant_id=%s actor_id=%s provider=gemini result=VALID",
        principal.active_tenant_id, principal.actor_id,
    )
    return _credential_view(metadata, source="configuration")

class CorrectRequest(BaseModel):
    values: dict = Field(default_factory=dict)


class FinalizeRequest(BaseModel):
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


def _view(row):
    return {
        "id": row.id,
        "document_id": row.document_id,
        "line_id": row.line_id,
        "reason_code": row.reason_code,
        "status": row.status,
        "original_value": row.original_value_json,
        "suggested_value": row.suggested_value_json,
        "final_value": row.final_value_json,
        "reviewer_id": row.reviewer_id,
        "reviewed_at": row.reviewed_at,
    }


def _daily_view(row):
    return {
        "id": row.id,
        "business_date": row.business_date,
        "status": row.status,
        "ready": row.ready,
        "finalized": row.finalized,
        "forced": row.forced,
        "blockers": row.snapshot.get("blockers", []),
        "report": row.snapshot,
        "finalized_at": row.finalized_at,
        "finalized_by": row.finalized_by,
    }


@router.get("/reviews")
def list_reviews(
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    return {
        "items": [
            _view(row)
            for row in InventoryReviewService(SessionLocal).list(principal.active_tenant_id)
        ]
    }


@router.get("/reviews/{review_id}")
def get_review(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    row = InventoryReviewService(SessionLocal).get(principal.active_tenant_id, review_id)
    if row is None:
        raise HTTPException(404, detail={"code": "inventory_review_not_found"})
    return _view(row)


def _mutate(review_id, action, values, principal):
    try:
        return _view(
            InventoryReviewService(SessionLocal).mutate(
                principal.active_tenant_id, review_id, action, principal.actor_id, values
            )
        )
    except LookupError:
        raise HTTPException(404, detail={"code": "inventory_review_not_found"})
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)})


@router.post("/reviews/{review_id}/approve")
def approve(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "approve", None, principal)


@router.post("/reviews/{review_id}/correct")
def correct(
    review_id: str,
    body: CorrectRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "correct", body.values, principal)


@router.post("/reviews/{review_id}/request-reupload")
def request_reupload(
    review_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_REVIEW_PERMISSION)),
):
    return _mutate(review_id, "request_reupload", None, principal)


@router.get("/daily-runs/{business_date}")
def get_daily_run(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    result = InventoryDailyRunService(SessionLocal).get(
        principal.active_tenant_id, business_date
    )
    if result is None:
        raise HTTPException(404, detail={"code": "inventory_daily_run_not_found"})
    return _daily_view(result)


@router.get("/daily-runs/{business_date}/report")
def get_daily_report(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return InventoryDailyReportService(SessionLocal).generate(
            principal.active_tenant_id, business_date
        )
    except LookupError as exc:
        raise HTTPException(404, detail={"code": "inventory_daily_run_not_found"}) from exc
    except DailyReportNotFinalized as exc:
        raise HTTPException(409, detail={"code": "inventory_daily_run_not_finalized"}) from exc

@router.post("/daily-runs/{business_date}/finalize")
def finalize_daily_run(
    business_date: date,
    body: FinalizeRequest,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    service = InventoryDailyRunService(SessionLocal)
    try:
        result = service.finalize(
            principal.active_tenant_id,
            business_date,
            actor_id=principal.actor_id,
            force=body.force,
            reason=body.reason,
        )
    except DailyRunBlocked as exc:
        raise HTTPException(
            409,
            detail={
                "code": "inventory_daily_run_not_ready",
                "message": "Inventory day cannot be finalized while blockers remain",
                "report": exc.snapshot,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    return _daily_view(result)


def _export_service():
    service = InventoryExportService(SessionLocal)
    # Keep router fakes used by API tests backward compatible while the real
    # service receives the production shadow-mode guard.
    try:
        service.shadow_mode = get_settings().INVENTORY_SHADOW_MODE
    except AttributeError:
        pass
    return service


def _export_view(row):
    return {
        "id": row.id, "business_date": row.business_date, "status": row.status,
        "main_drive_file_id": row.main_drive_file_id,
        "backup_drive_file_id": row.backup_drive_file_id,
        "content_sha256": row.content_sha256, "completed_at": row.completed_at,
        "error_code": row.error_code,
        "archive_status": row.archive_status,
        "archive_error_code": row.archive_error_code,
    }


@router.get("/exports/{business_date}")
def get_export(business_date: date, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_EXPORT_PERMISSION))):
    result = _export_service().get(principal.active_tenant_id, business_date)
    if result is None:
        raise HTTPException(404, detail={"code": "inventory_export_not_found"})
    return _export_view(result)


@router.post("/exports/{business_date}")
def export(business_date: date, principal: CurrentPrincipal = Depends(require_permission(INVENTORY_EXPORT_PERMISSION))):
    try:
        return _export_view(_export_service().export(principal.active_tenant_id, business_date, principal.actor_id))
    except InventoryExportFailure as exc:
        code = str(exc)
        status = 409 if code == "inventory_daily_run_not_finalized" else 422
        raise HTTPException(status, detail={"code": code}) from exc
