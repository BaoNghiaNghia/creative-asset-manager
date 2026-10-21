"""Authenticated manager evidence certification, never activation or writeback."""
from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.inventory.daily_sheet.catinat_shift_readiness import load_evening_readiness
from app.modules.inventory.daily_sheet.google_client import DailySheetProviderError
from app.modules.inventory.daily.catinat_catalog_mapping import CatinatCatalogMappingPreview
from app.modules.inventory.daily.catinat_catalog_coverage import catalog_evidence_coverage
from app.modules.inventory.daily.catinat_conversion_review import read_only_conversion_review
from app.modules.inventory.daily.catinat_count_confirmations import CatinatCountBlocked
from app.modules.inventory.daily.catinat_catalog_certifications import (
    CatinatCatalogCertificationService, CatinatCertificationBlocked,
)
from app.modules.inventory.daily.catinat_approval_router import _manager_guard
from app.modules.inventory.permissions import (
    INVENTORY_FINALIZE_PERMISSION, INVENTORY_READ_PERMISSION,
)

router = APIRouter(prefix="/catinat/catalog-certifications",
                   tags=["inventory-catinat-catalog-certifications"])


class CertificationBody(BaseModel):
    approval_request_id: str = Field(min_length=1, max_length=36)
    item_id: str = Field(min_length=1, max_length=36)
    location_id: str = Field(min_length=1, max_length=36)
    note: str = Field(min_length=8, max_length=1000)


def _service() -> CatinatCatalogCertificationService:
    return CatinatCatalogCertificationService(SessionLocal)


@router.get("")
def list_certifications(
    business_date: date,
    limit: int = Query(default=100, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    return {"items": _service().list(
        tenant_id=principal.active_tenant_id, business_date=business_date, limit=limit,
    ), "mapping_activated": False, "ready_to_sign": False, "sheets_writes": 0}


@router.get("/coverage")
def get_catalog_certification_coverage(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Read-only per-warehouse certification progress; never a close gate."""
    try:
        ready = load_evening_readiness(
            principal.active_tenant_id, business_date, sessions=SessionLocal,
        )
        report = CatinatCatalogMappingPreview(SessionLocal).inspect(
            tenant_id=principal.active_tenant_id,
            business_date=business_date, current_readiness=lambda: ready,
        )
        return catalog_evidence_coverage(
            SessionLocal, tenant_id=principal.active_tenant_id,
            business_date=business_date, report=report,
        )
    except CatinatCountBlocked as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.get("/conversion-review")
def get_package_conversion_review(
    business_date: date,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_READ_PERMISSION)),
):
    """Inspect manager-approved package evidence without inventory/Sheets writes."""
    try:
        ready = load_evening_readiness(
            principal.active_tenant_id, business_date, sessions=SessionLocal,
        )
        report = CatinatCatalogMappingPreview(SessionLocal).inspect(
            tenant_id=principal.active_tenant_id, business_date=business_date,
            current_readiness=lambda: ready,
        )
        return read_only_conversion_review(
            SessionLocal, tenant_id=principal.active_tenant_id,
            business_date=business_date, report=report,
        )
    except CatinatCountBlocked as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


@router.post("")
def certify_catalog_mapping(
    body: CertificationBody,
    principal: CurrentPrincipal = Depends(require_permission(INVENTORY_FINALIZE_PERMISSION)),
):
    _manager_guard(principal)
    try:
        return _service().certify(
            tenant_id=principal.active_tenant_id, actor_id=principal.user_id,
            request_id=body.approval_request_id, item_id=body.item_id,
            location_id=body.location_id, note=body.note,
            current_readiness=lambda day: load_evening_readiness(
                principal.active_tenant_id, day, sessions=SessionLocal,
            ),
            current_preview=lambda day: CatinatCatalogMappingPreview(SessionLocal).inspect(
                tenant_id=principal.active_tenant_id, business_date=day,
                current_readiness=lambda: load_evening_readiness(
                    principal.active_tenant_id, day, sessions=SessionLocal,
                ),
            ),
        )
    except CatinatCertificationBlocked as exc:
        code = exc.code
        status = (404 if code == "approval_not_found"
                  else 403 if code == "certifying_manager_mismatch"
                  else 422 if code in {"missing_certification_identity", "certification_note_required"}
                  else 409)
        raise HTTPException(status, detail={"code": code}) from exc
    except DailySheetProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc
