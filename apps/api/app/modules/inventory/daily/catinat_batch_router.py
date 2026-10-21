"""Staff-attested batch facts and manager waste-disposal proposals, no stock writes."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_any_permission, require_permission,
)
from app.modules.inventory.daily.catinat_batch_evidence import (
    CatinatBatchBlocked, CatinatBatchEvidenceService,
)
from app.modules.inventory.daily.catinat_count_router import _staff_guard
from app.modules.inventory.daily_sheet.catinat_shift_readiness import load_evening_readiness
from app.modules.inventory.daily_sheet.catinat_preview import CatinatPreviewError
from app.modules.inventory.daily_sheet.google_client import DailySheetProviderError
from app.modules.inventory.permissions import (
    INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_READ_PERMISSION, INVENTORY_REVIEW_PERMISSION,
)

router = APIRouter(prefix="/catinat/batch-evidence", tags=["catinat-batch-evidence"])


class BatchEvidenceInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    business_date: date
    expected_source_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    sheet: str = Field(min_length=1, max_length=128)
    cell: str = Field(min_length=2, max_length=16)
    lot_reference: str = Field(min_length=1, max_length=128)
    event_kind: str = Field(pattern=r"^(prepared|opened)$")
    event_at: datetime
    package_expiry_at: datetime | None = None
    note: str = Field(min_length=8, max_length=1000)


class DisposalReviewInput(BaseModel):
    business_date: date
    expected_source_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    quantity: str = Field(min_length=1, max_length=60)
    reason: str = Field(min_length=8, max_length=1000)


def _service() -> CatinatBatchEvidenceService:
    return CatinatBatchEvidenceService(SessionLocal)


def _current(tenant_id: str, business_date: date) -> dict:
    return load_evening_readiness(tenant_id, business_date, sessions=SessionLocal)


def _error(exc: CatinatBatchBlocked) -> HTTPException:
    if exc.code in {"batch_evidence_not_found"}:
        status = 404
    elif exc.code in {
        "google_sheet_changed_reconfirm_required", "batch_source_evidence_stale",
        "batch_evidence_idempotency_conflict", "batch_evidence_concurrent_conflict",
    }:
        status = 409
    else:
        status = 422
    return HTTPException(status, detail={"code": exc.code})


@router.post("")
def record_batch_evidence(
    body: BatchEvidenceInput,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff_guard(principal)
    try:
        return _service().record(
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id, idempotency_key=body.idempotency_key,
            business_date=body.business_date, expected_source_hash=body.expected_source_hash,
            sheet=body.sheet, cell=body.cell,
            lot_reference=body.lot_reference, event_kind=body.event_kind,
            event_at=body.event_at, package_expiry_at=body.package_expiry_at,
            note=body.note,
            current_readiness=lambda: _current(principal.active_tenant_id, body.business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("")
def list_batch_evidence(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _service().list(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: _current(principal.active_tenant_id, business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.post("/{batch_id}/request-disposal")
def request_batch_disposal(
    batch_id: str, body: DisposalReviewInput,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff_guard(principal)
    try:
        return _service().request_disposal(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            batch_id=batch_id, business_date=body.business_date,
            expected_source_hash=body.expected_source_hash,
            idempotency_key=body.idempotency_key, quantity=body.quantity,
            reason=body.reason,
            current_readiness=lambda: _current(principal.active_tenant_id, body.business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc
