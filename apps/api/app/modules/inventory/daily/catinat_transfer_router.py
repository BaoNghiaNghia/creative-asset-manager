"""Catinat Q32 handoff / receiver verification endpoints.

No automatic Sheets mutation or accounting ledger credits.
"""
from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_any_permission, require_permission,
)
from app.modules.inventory.daily.catinat_transfers import (
    CatinatTransferBlocked, CatinatTransferService,
)
from app.modules.inventory.permissions import (
    INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_REVIEW_PERMISSION, INVENTORY_READ_PERMISSION,
)

router = APIRouter(prefix="/catinat/transfers", tags=["catinat-transfers"])


class DispatchBody(BaseModel):
    reference_key: str = Field(min_length=8, max_length=128)
    business_date: date
    item_id: str = Field(min_length=1, max_length=36)
    source_location_id: str = Field(min_length=1, max_length=36)
    destination_location_id: str = Field(min_length=1, max_length=36)
    unit: str = Field(min_length=1, max_length=64)
    quantity: str = Field(min_length=1, max_length=64)


class ReceiptBody(BaseModel):
    event_key: str = Field(min_length=8, max_length=128)
    quantity: str = Field(min_length=1, max_length=64)


def _service() -> CatinatTransferService:
    return CatinatTransferService(SessionLocal)


def _staff(principal: CurrentPrincipal) -> None:
    if principal.platform_admin:
        return
    if not principal.effective_roles.intersection({
        "operator", "tenant_admin", "shift_leader",
        "catinat_staff", "catinat_shift_lead", "manager", "catinat_manager",
    }):
        raise HTTPException(403, detail={"code": "catinat_staff_role_required"})


def _error(exc: CatinatTransferBlocked) -> HTTPException:
    code = exc.code
    status = (404 if code == "transfer_not_found"
              else 409 if code in {
                  "receipt_exceeds_in_transit", "transfer_idempotency_conflict",
                  "transfer_event_idempotency_conflict", "concurrent_dispatch_conflict",
                  "concurrent_receipt_conflict",
              } else 422)
    return HTTPException(status, detail={"code": code})


@router.post("")
def dispatch_catinat_transfer(
    body: DispatchBody,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff(principal)
    try:
        return _service().dispatch(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            **body.model_dump(),
        )
    except CatinatTransferBlocked as exc:
        raise _error(exc) from exc


@router.post("/{transfer_id}/receive")
def receive_catinat_transfer(
    transfer_id: str, body: ReceiptBody,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff(principal)
    try:
        return _service().receive(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            transfer_id=transfer_id, **body.model_dump(),
        )
    except CatinatTransferBlocked as exc:
        raise _error(exc) from exc


@router.get("")
def list_catinat_transfers(
    business_date: date | None = None,
    pending_only: bool = False,
    limit: int = Query(default=100, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return {"items": _service().list(
            tenant_id=principal.active_tenant_id,
            business_date=business_date, pending_only=pending_only, limit=limit,
        )}
    except CatinatTransferBlocked as exc:
        raise _error(exc) from exc


@router.get("/{transfer_id}")
def get_catinat_transfer(
    transfer_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _service().get(
            tenant_id=principal.active_tenant_id, transfer_id=transfer_id,
        )
    except CatinatTransferBlocked as exc:
        raise _error(exc) from exc
