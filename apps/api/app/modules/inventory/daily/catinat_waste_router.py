"""Catinat signed physical waste evidence, not an inventory stock writer."""
from __future__ import annotations

from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_any_permission, require_permission,
)
from app.modules.inventory.daily.catinat_count_router import _staff_guard
from app.modules.inventory.daily.catinat_approval_router import _manager_guard
from app.modules.inventory.daily.catinat_batch_evidence import CatinatBatchBlocked
from app.modules.inventory.daily.catinat_waste_evidence import CatinatWasteEvidenceService
from app.modules.inventory.daily_sheet.catinat_shift_readiness import load_evening_readiness
from app.modules.inventory.daily_sheet.catinat_preview import CatinatPreviewError
from app.modules.inventory.daily_sheet.google_client import DailySheetProviderError
from app.modules.inventory.permissions import (
    INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
    INVENTORY_FINALIZE_PERMISSION, INVENTORY_READ_PERMISSION,
)

router = APIRouter(prefix="/catinat/waste-evidence", tags=["catinat-waste-evidence"])


class WitnessBody(BaseModel):
    approval_request_id: str = Field(min_length=1, max_length=36)
    business_date: date
    expected_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    quantity: str = Field(min_length=1, max_length=60)
    observed_at: datetime
    method: str = Field(min_length=8, max_length=100)
    evidence_reference: str = Field(min_length=8, max_length=2048)
    note: str = Field(min_length=8, max_length=1000)


class CountersignBody(BaseModel):
    business_date: date
    expected_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    note: str = Field(min_length=8, max_length=1000)


def _service():
    return CatinatWasteEvidenceService(SessionLocal)


def _source_for(tenant_id, day):
    return load_evening_readiness(tenant_id, day, sessions=SessionLocal)


def _error(exc):
    code = exc.code
    return HTTPException(
        404 if code.endswith("_not_found") else
        409 if any(x in code for x in ("changed", "stale", "conflict", "mismatch", "tampered")) else 422,
        detail={"code": code},
    )


@router.post("/witness")
def witness_physical_disposal(
    body: WitnessBody,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff_guard(principal)
    try:
        return _service().witness(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            approval_request_id=body.approval_request_id, business_date=body.business_date,
            expected_source_hash=body.expected_source_hash,
            idempotency_key=body.idempotency_key, quantity=body.quantity,
            observed_at=body.observed_at, method=body.method,
            evidence_reference=body.evidence_reference, note=body.note,
            current_readiness=lambda: _source_for(principal.active_tenant_id, body.business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.post("/{witness_id}/countersign")
def manager_countersign_physical_disposal(
    witness_id: str, body: CountersignBody,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    _manager_guard(principal)
    try:
        return _service().countersign(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            witness_id=witness_id, business_date=body.business_date,
            expected_source_hash=body.expected_source_hash, note=body.note,
            current_readiness=lambda: _source_for(principal.active_tenant_id, body.business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("")
def list_waste_evidence(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _service().list(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: _source_for(principal.active_tenant_id, business_date),
        )
    except CatinatBatchBlocked as exc:
        raise _error(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc
