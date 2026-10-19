"""BD-01: gated ingestion of authorized metadata-only exports, not web scans.

Never fetches submitted URLs, downloads images or starts a worker. All imported
facts are marked unverified. Read APIs provide only tenant-scoped evidence.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-evidence"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")
SHA256 = r"^[a-fA-F0-9]{64}$"
KEY = r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,254}$"


class GalleryImport(BaseModel):
    model_config = {"extra": "forbid"}
    sequence_index: int = Field(ge=0, le=99)
    asset_sha256: str = Field(pattern=SHA256)
    source_url_sha256: str = Field(pattern=SHA256)
    role: Literal["hero", "detail", "lifestyle", "ugc", "information", "variation", "unknown"] = "unknown"
    variant_key: str | None = Field(default=None, max_length=128)
    rights_state: Literal["unknown"] = "unknown"  # export metadata cannot verify usage rights


class ListingImport(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    listing_key: str = Field(pattern=KEY)
    source_revision: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    export_reference: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    title: str = Field(min_length=2, max_length=300)
    category: str = Field(min_length=1, max_length=80)
    captured_at: datetime
    images: list[GalleryImport] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def gallery_is_contiguous(self):
        # Preserve the exact source gallery order. Missing positions cannot be invented.
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        indices = [item.sequence_index for item in self.images]
        if indices != list(range(len(indices))):
            raise ValueError("Gallery indices must be contiguous in original order starting at zero")
        return self


def shop_for_tenant(session: Session, tenant_id: str, shop_id: str) -> BrandShopModel:
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == shop_id, BrandShopModel.tenant_id == tenant_id,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    return shop


def payload_digest(payload: ListingImport) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def present_snapshot(row: BrandListingSnapshotModel, image_count: int) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id,
        "listing_key": row.listing_key, "source_revision": row.source_revision,
        "export_reference": row.export_reference, "title": row.title, "category": row.category,
        "source_kind": row.source_kind, "evidence_state": row.evidence_state,
        "coverage_state": row.coverage_state, "captured_at": row.captured_at.isoformat(),
        "image_count": image_count,
    }


@router.post("/shops/{shop_id}/listings/preview")
def preview_listing_import(
    shop_id: str, payload: ListingImport, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    """Validate an authorized export without persisting, fetching or queuing.

    The preview checks the exact same strict schema and canonical content hash
    as import_listing. A shop declaration/review never upgrades access_mode.
    """
    tenant = principal.active_tenant_id
    shop = shop_for_tenant(session, tenant, shop_id)
    digest = payload_digest(payload)
    existing = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.listing_key == payload.listing_key,
        BrandListingSnapshotModel.source_revision == payload.source_revision,
    ))
    outcome = (
        "revision_conflict" if existing is not None and existing.content_sha256 != digest
        else "already_imported" if existing is not None
        else "new"
    )
    allowed = shop.access_mode in ("owned", "authorized")
    blockers = (
        ["owned_or_authorized_shop_required"] if not allowed else []
    ) + (["source_revision_conflict"] if outcome == "revision_conflict" else [])
    return {
        "shop_id": shop_id, "listing_key": payload.listing_key,
        "source_revision": payload.source_revision,
        "export_reference": payload.export_reference,
        "content_sha256": digest,
        "image_count": len(payload.images),
        "source_kind": "authorized_export", "evidence_state": "imported_unverified",
        "coverage_state": "unknown", "gallery_order_valid": True,
        "state": outcome,
        "can_import": allowed and outcome == "new",
        "blockers": blockers,
        "rights_verified": False, "scan_enabled": False, "dispatchable": False,
        "persisted": False, "network_fetched": False,
    }


@router.post("/shops/{shop_id}/listings/import", status_code=201)
def import_listing(
    shop_id: str, payload: ListingImport, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    shop = shop_for_tenant(session, tenant, shop_id)
    if shop.access_mode not in ("owned", "authorized"):
        raise HTTPException(403, detail="Only owned or authorized shops can import exports")
    # Import metadata only. Submitted URLs, user-provided tenant IDs, and raw media
    # bytes are prohibited. The authorized export is not evidence of sales or style.
    digest = payload_digest(payload)
    criteria = (
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.listing_key == payload.listing_key,
        BrandListingSnapshotModel.source_revision == payload.source_revision,
    )
    existing = session.scalar(select(BrandListingSnapshotModel).where(*criteria))
    if existing is not None:
        if existing.content_sha256 != digest:
            raise HTTPException(409, detail="Source revision already exists with different content")
        count = session.scalar(select(func.count()).select_from(BrandListingImageModel).where(
            BrandListingImageModel.tenant_id == tenant,
            BrandListingImageModel.shop_id == shop_id,
            BrandListingImageModel.snapshot_id == existing.id,
        )) or 0
        return {"item": present_snapshot(existing, count), "replayed": True}

    snapshot = BrandListingSnapshotModel(
        tenant_id=tenant, shop_id=shop_id, listing_key=payload.listing_key,
        source_revision=payload.source_revision,
        export_reference=payload.export_reference,
        title=payload.title, category=payload.category,
        source_kind="authorized_export", evidence_state="imported_unverified",
        coverage_state="unknown", captured_at=payload.captured_at,
        imported_by=principal.actor_id, content_sha256=digest,
    )
    session.add(snapshot)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Source revision was written concurrently; retry") from exc
    for item in payload.images:
        session.add(BrandListingImageModel(
            tenant_id=tenant, shop_id=shop_id, snapshot_id=snapshot.id,
            sequence_index=item.sequence_index, asset_sha256=item.asset_sha256.lower(),
            source_url_sha256=item.source_url_sha256.lower(), role=item.role,
            variant_key=item.variant_key, rights_state=item.rights_state,
        ))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Concurrent identical imports are retryable using the same revision.
        raise HTTPException(409, detail="Source revision was written concurrently; retry") from exc
    return {"item": present_snapshot(snapshot, len(payload.images)), "replayed": False}


@router.get("/shops/{shop_id}/listings")
def list_listing_evidence(
    shop_id: str, limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    criteria = (BrandListingSnapshotModel.tenant_id == tenant,
                BrandListingSnapshotModel.shop_id == shop_id)
    total = session.scalar(select(func.count()).select_from(BrandListingSnapshotModel).where(*criteria)) or 0
    rows = session.scalars(select(BrandListingSnapshotModel).where(*criteria).order_by(
        BrandListingSnapshotModel.captured_at.desc(), BrandListingSnapshotModel.id.desc()
    ).offset(offset).limit(limit)).all()
    ids = [row.id for row in rows]
    counts: dict[str, int] = {}
    if ids:
        counts = dict(session.execute(
            select(BrandListingImageModel.snapshot_id, func.count())
            .where(BrandListingImageModel.tenant_id == tenant,
                   BrandListingImageModel.shop_id == shop_id,
                   BrandListingImageModel.snapshot_id.in_(ids))
            .group_by(BrandListingImageModel.snapshot_id)
        ).all())
    return {"items": [present_snapshot(row, counts.get(row.id, 0)) for row in rows],
            "count": total, "limit": limit, "offset": offset,
            "scope": "authorized_export", "scan_enabled": False}


@router.get("/shops/{shop_id}/listings/{snapshot_id}/gallery")
def listing_gallery(
    shop_id: str, snapshot_id: str, session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    snapshot = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.id == snapshot_id,
        BrandListingSnapshotModel.tenant_id == tenant,
        BrandListingSnapshotModel.shop_id == shop_id,
    ))
    if snapshot is None:
        raise HTTPException(404, detail="Listing snapshot not found")
    images = session.scalars(select(BrandListingImageModel).where(
        BrandListingImageModel.tenant_id == tenant,
        BrandListingImageModel.shop_id == shop_id,
        BrandListingImageModel.snapshot_id == snapshot_id,
    ).order_by(BrandListingImageModel.sequence_index)).all()
    return {"listing": present_snapshot(snapshot, len(images)),
            "images": [
                {"sequence_index": image.sequence_index, "asset_sha256": image.asset_sha256,
                 "source_url_sha256": image.source_url_sha256, "role": image.role,
                 "variant_key": image.variant_key, "rights_state": image.rights_state,
                 "media_available": False}
                for image in images
            ]}
