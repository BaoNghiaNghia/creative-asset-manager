"""Read-only result manifest preflight; never accepts raw scan evidence.

It binds a bounded metadata manifest to the existing staged task + snapshot.
No scanner job is activated, no evidence is imported, and no rights are granted.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scan_result_contract import (
    MAX_RESULT_IMAGES, MAX_RESULT_PAGES, ResultManifest,
    manifest_digest, valid_capture_window,
)

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-scan-result-preflight"])
CONFIGURE = require_permission("ai_provider.configure")


class PreviewScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    manifest: ResultManifest


@router.post("/shops/{shop_id}/scan-results/preview")
def preview_scan_result(
    shop_id: str, body: PreviewScanResult, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
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
    if task.status != "staged" or task.task_kind != "verify_listing_evidence":
        raise HTTPException(409, detail="Only staged listing-verification tasks can be previewed")
    snapshot = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.id == task.snapshot_id,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.tenant_id == tenant,
    ))
    if snapshot is None:
        raise HTTPException(409, detail="Listing snapshot no longer available")
    if (body.manifest.listing_key != snapshot.listing_key
            or shop.access_mode not in ("owned", "authorized")):
        raise HTTPException(409, detail="Scan result source identity not eligible")
    if not valid_capture_window(body.manifest.captured_at, now=now_utc()):
        raise HTTPException(422, detail="scan_result_capture_time_outside_window")

    digest = manifest_digest(
        tenant_id=tenant, shop_id=shop_id, task_id=task.id,
        snapshot_id=snapshot.id, snapshot_sha256=snapshot.content_sha256,
        manifest=body.manifest,
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id, "task_id": task.id,
        "snapshot_id": snapshot.id,
        "manifest_sha256": digest,
        "gallery_items": len(body.manifest.images),
        "page_count": body.manifest.page_count,
        "limits": {"max_pages": MAX_RESULT_PAGES, "max_gallery_items": MAX_RESULT_IMAGES},
        "evidence_state": "imported_unverified",
        "rights_state": "unknown",
        "provenance_verified": False,
        "marketplace_authorization_verified": False,
        "persisted": False, "receipt_issued": False,
        "job": None, "dispatchable": False, "scan_enabled": False,
        "blockers": ["source_provenance_not_verified",
                     "marketplace_authorization_not_verified",
                     "scan_result_ingestion_not_deployed",
                     "scan_task_dispatch_not_deployed"],
    }
