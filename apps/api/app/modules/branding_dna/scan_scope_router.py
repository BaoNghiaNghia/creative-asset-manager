"""Read-only scope validation of candidate future browser navigation.

No route issues a lease, navigates a browser, resolves DNS or records the URL.
The administrative preflight cannot be used as evidence of marketplace rights.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.scan_scope_service import (
    evaluate_storefront_url, inactive_scan_budget,
)

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-scan-scope"])
CONFIGURE = require_permission("ai_provider.configure")


class PreviewScanScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    task_id: UUID
    candidate_url: str = Field(min_length=12, max_length=2048)


@router.post("/shops/{shop_id}/scan-scope/preview")
def preview_scan_scope(
    shop_id: str, body: PreviewScanScope, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    """A bounded, non-executable decision, never a worker-facing job."""
    tenant = principal.active_tenant_id
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    task = session.scalar(select(BrandScanTaskModel).where(
        BrandScanTaskModel.id == str(body.task_id),
        BrandScanTaskModel.shop_id == shop_id,
        BrandScanTaskModel.tenant_id == tenant,
    ))
    if task is None:
        raise HTTPException(404, detail="Scan task not found")
    if task.status != "staged":
        raise HTTPException(409, detail="Only staged scan tasks can be previewed")
    snapshot = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.id == task.snapshot_id,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.tenant_id == tenant,
    ))
    if snapshot is None:
        raise HTTPException(409, detail="Listing snapshot no longer available")
    valid, code = evaluate_storefront_url(
        platform=shop.platform,
        registered_url=shop.storefront_url,
        candidate_url=body.candidate_url,
    )
    declared = shop.access_mode in ("owned", "authorized")
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id,
        "task_id": task.id,
        "candidate_within_storefront": valid,
        "scope_reason": code,
        "shop_access_declared": declared,
        "limits": inactive_scan_budget(),
        "blockers": [
            "scan_dispatch_disabled",
            "marketplace_authorization_unverified",
            "navigation_not_authorized",
        ] + ([] if declared else ["shop_access_not_declared"]),
        # Do not include submitted URLs, source bytes or browser instructions.
        "job": None, "dispatchable": False, "scan_enabled": False,
        "marketplace_authorization_verified": False,
    }
