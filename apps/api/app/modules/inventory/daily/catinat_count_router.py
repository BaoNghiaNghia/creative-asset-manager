"""Authenticated, source-bound count confirmation API (NOT a closing endpoint)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_any_permission, require_permission,
)
from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, CatinatCountConfirmationService,
)
from app.modules.inventory.daily_sheet.catinat_shift_readiness import load_evening_readiness
from app.modules.inventory.daily_sheet.catinat_preview import CatinatPreviewError
from app.modules.inventory.daily_sheet.google_client import DailySheetProviderError
from app.modules.inventory.permissions import (
    INVENTORY_CONTROL_PERMISSION, INVENTORY_FINALIZE_PERMISSION,
    INVENTORY_READ_PERMISSION, INVENTORY_REVIEW_PERMISSION,
)

router = APIRouter(prefix="/catinat/shift/count-confirmations", tags=["catinat-counts"])


class PhysicalCell(BaseModel):
    sheet: Literal["KHO PHA CHẾ", "PHÒNG PHA CHẾ", "KHO PHÒNG RANG"]
    cell: str = Field(pattern=r"^[A-Z]{1,2}[1-9][0-9]{0,3}$", max_length=16)


class ConfirmPhysicalCells(BaseModel):
    business_date: date
    expected_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)
    cells: list[PhysicalCell] = Field(min_length=1, max_length=50)


def _service() -> CatinatCountConfirmationService:
    return CatinatCountConfirmationService(SessionLocal)


def _current(tenant_id: str, business_date: date) -> dict:
    return load_evening_readiness(tenant_id, business_date, sessions=SessionLocal)


def _staff_guard(principal: CurrentPrincipal) -> None:
    """An inventory permission alone does not establish staff authority."""
    if principal.platform_admin:
        return
    if not principal.effective_roles.intersection({
        "operator", "tenant_admin", "shift_leader", "catinat_staff",
        "catinat_shift_lead", "manager", "catinat_manager",
    }):
        raise HTTPException(403, detail={"code": "catinat_staff_role_required"})


def _blocked(exc: CatinatCountBlocked) -> HTTPException:
    code = exc.code
    status = 409 if code in {
        "google_sheet_changed_reconfirm_required", "physical_cell_already_confirmed_by_other",
        "concurrent_physical_confirmation_conflict", "duplicate_source_count_identity",
    } else 422
    return HTTPException(status, detail={"code": code})


@router.post("")
def confirm_physical_count_cells(
    body: ConfirmPhysicalCells,
    principal: CurrentPrincipal = Depends(require_any_permission(
        INVENTORY_REVIEW_PERMISSION, INVENTORY_CONTROL_PERMISSION,
        INVENTORY_FINALIZE_PERMISSION,
    )),
):
    _staff_guard(principal)
    try:
        return _service().confirm(
            tenant_id=principal.active_tenant_id,
            actor_id=principal.user_id,
            business_date=body.business_date,
            expected_source_hash=body.expected_source_hash,
            cells=[item.model_dump() for item in body.cells],
            current_readiness=lambda: _current(principal.active_tenant_id, body.business_date),
        )
    except CatinatCountBlocked as exc:
        raise _blocked(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("")
def get_confirmed_physical_count_cells(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    try:
        return _service().state(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: _current(principal.active_tenant_id, business_date),
        )
    except CatinatCountBlocked as exc:
        raise _blocked(exc) from exc
    except CatinatPreviewError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc
