"""Authenticated Catinat manager review API, without inventory/Sheets writes."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_any_permission, require_permission,
)
from app.modules.inventory.daily.catinat_approvals import (
    CatinatApprovalBlocked, CatinatApprovalService,
)
from app.modules.inventory.permissions import (
    INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_READ_PERMISSION, INVENTORY_REVIEW_PERMISSION,
)

router = APIRouter(prefix="/catinat/approvals", tags=["inventory-catinat-approvals"])


class ApprovalRequestBody(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    business_date: date
    kind: Literal[
        "discrepancy", "catalog_mapping", "package_conversion",
        "post_close_adjustment", "transfer_exception", "waste_disposal",
    ]
    subject_ref: str = Field(min_length=1, max_length=255)
    proposal: dict[str, Any]
    reason: str = Field(min_length=8, max_length=1000)


class ApprovalDecisionBody(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=8, max_length=1000)


def _service() -> CatinatApprovalService:
    return CatinatApprovalService(SessionLocal)


def _handle(exc: CatinatApprovalBlocked) -> HTTPException:
    code = exc.code
    status = (404 if code == "approval_not_found"
              else 403 if code == "self_approval_forbidden"
              else 409 if code in {"approval_idempotency_conflict", "approval_already_decided"}
              else 422)
    return HTTPException(status, detail={"code": code})


def _manager_guard(principal: CurrentPrincipal) -> None:
    """Q44: broad Inventory-finalize permission alone is not a manager role."""
    if principal.platform_admin:
        return
    if not principal.effective_roles.intersection(
        {"tenant_admin", "catinat_manager", "manager"}
    ):
        raise HTTPException(403, detail={"code": "catinat_manager_role_required"})


@router.post("")
def submit_catinat_approval(
    body: ApprovalRequestBody,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
    )),
):
    try:
        return _service().request(
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            idempotency_key=body.idempotency_key,
            business_date=body.business_date,
            kind=body.kind,
            subject_ref=body.subject_ref,
            proposal=body.proposal,
            reason=body.reason,
        )
    except CatinatApprovalBlocked as exc:
        raise _handle(exc) from exc


@router.get("")
def list_catinat_approvals(
    status: Literal["pending", "approved", "rejected"] | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return {"items": _service().list(
            tenant_id=principal.active_tenant_id, status=status, limit=limit,
        )}
    except CatinatApprovalBlocked as exc:
        raise _handle(exc) from exc


@router.get("/{request_id}")
def get_catinat_approval(
    request_id: str,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _service().get(
            tenant_id=principal.active_tenant_id, request_id=request_id,
        )
    except CatinatApprovalBlocked as exc:
        raise _handle(exc) from exc


@router.post("/{request_id}/decision")
def decide_catinat_approval(
    request_id: str, body: ApprovalDecisionBody,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    _manager_guard(principal)
    try:
        return _service().decide(
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            request_id=request_id,
            decision=body.decision,
            reason=body.reason,
        )
    except CatinatApprovalBlocked as exc:
        raise _handle(exc) from exc
