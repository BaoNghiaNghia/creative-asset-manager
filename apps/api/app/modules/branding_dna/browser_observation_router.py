"""Operator-reviewed local DOM pilot reports (quarantined, unverified).

This endpoint never opens a browser, creates a scan task, verifies image
rights, or promotes observations to independently verified DNA evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.browser_observation_model import BrandBrowserObservationModel
from app.modules.branding_dna.evidence_router import shop_for_tenant

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-browser-observations"])
READ = require_permission("ai_operations.read")
CONFIGURE = require_permission("ai_provider.configure")
SHA256 = r"^[0-9a-f]{64}$"
KEY = r"^[a-zA-Z0-9_.:-]{8,128}$"


class ObservedListing(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    listing_key: str = Field(min_length=5, max_length=32)
    title: str = Field(min_length=2, max_length=300)
    observed_page_sha256: str = Field(pattern=SHA256)
    image_url_sha256: list[str] = Field(min_length=0, max_length=12)
    image_count: int = Field(ge=0, le=12)
    source_kind: Literal["browser_dom_observation"]
    rights_state: Literal["unknown"]
    sales_state: Literal["unknown"]
    coverage_state: Literal["partial"]
    source_verification: Literal["unverified"]

    @model_validator(mode="after")
    def image_counts(self):
        if self.image_count != len(self.image_url_sha256):
            raise ValueError("Image digest count mismatch")
        if any(len(d) != 64 or any(c not in "0123456789abcdef" for c in d)
               for d in self.image_url_sha256):
            raise ValueError("Invalid image URL digest")
        if len(set(self.image_url_sha256)) != len(self.image_url_sha256):
            raise ValueError("Image URL hashes must be unique")
        return self


class BrowserObservationInput(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    report_key: str = Field(pattern=KEY)
    status: Literal["preview_completed"]
    platform: Literal["amazon", "etsy"]
    shop_url_sha256: str = Field(pattern=SHA256)
    observed_at: datetime
    items: list[ObservedListing] = Field(min_length=1, max_length=5)
    observed_count: int = Field(ge=1, le=5)
    coverage_state: Literal["partial"]
    rights_verified: Literal[False]
    sales_verified: Literal[False]
    evidence_committed: Literal[False]
    scan_enabled: Literal[False]

    @model_validator(mode="after")
    def validate_observation(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at requires timezone")
        if len(self.items) != self.observed_count:
            raise ValueError("Observation count mismatch")
        if len({item.listing_key for item in self.items}) != len(self.items):
            raise ValueError("Duplicate listing keys")
        pattern = (
            r"^etsy:[0-9]{1,20}$" if self.platform == "etsy"
            else r"^amazon:[A-Z0-9]{10}$"
        )
        import re
        if any(not re.fullmatch(pattern, item.listing_key) for item in self.items):
            raise ValueError("Listing identifier doesn't match platform")
        return self


def fingerprint(body: BrowserObservationInput) -> str:
    serialized = json.dumps(body.model_dump(mode="json"), sort_keys=True,
                            separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def safe_metadata(row: BrandBrowserObservationModel) -> dict:
    return {
        "id": row.id, "shop_id": row.shop_id, "report_key": row.report_key,
        "content_sha256": row.content_sha256, "platform": row.platform,
        "status": row.status, "observed_at": row.observed_at.isoformat(),
        "observed_count": len(json.loads(row.items_json)),
        "evidence_state": "browser_observation_unverified",
        "rights_verified": False, "sales_verified": False,
        "scan_enabled": False, "dispatchable": False,
        "created_at": row.created_at.isoformat(),
    }


def check_submission(session: Session, tenant: str, shop_id: str,
                     body: BrowserObservationInput):
    shop = shop_for_tenant(session, tenant, shop_id)
    if shop.platform != body.platform:
        raise HTTPException(409, detail="Pilot report platform mismatch")
    canonical = hashlib.sha256(shop.storefront_url.encode("utf-8")).hexdigest()
    if body.shop_url_sha256 != canonical:
        raise HTTPException(409, detail="Pilot report shop URL mismatch")
    clock = datetime.now(timezone.utc)
    captured = body.observed_at.astimezone(timezone.utc)
    if not clock - timedelta(days=30) <= captured <= clock + timedelta(minutes=5):
        raise HTTPException(422, detail="Pilot report timestamp outside accepted window")
    if shop.status != "active" or shop.access_mode not in ("owned", "authorized"):
        raise HTTPException(403, detail="Reviewed active owned/authorized shop required")
    return shop


@router.post("/shops/{shop_id}/browser-observations", status_code=201)
def submit_browser_observation(
    shop_id: str, body: BrowserObservationInput, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(CONFIGURE),
):
    tenant = principal.active_tenant_id
    check_submission(session, tenant, shop_id, body)
    digest = fingerprint(body)
    existing = session.scalar(select(BrandBrowserObservationModel).where(
        BrandBrowserObservationModel.tenant_id == tenant,
        BrandBrowserObservationModel.shop_id == shop_id,
        BrandBrowserObservationModel.report_key == body.report_key,
    ))
    response.headers["Cache-Control"] = "no-store"
    if existing is not None:
        if existing.content_sha256 != digest:
            raise HTTPException(409, detail="Report key already used for different content")
        response.status_code = 200
        return {"item": safe_metadata(existing), "replayed": True}

    # Store only bounded structured metadata. Never upload media, external URLs
    # or a claimed verified state from an untrusted browser pilot report.
    items_json = json.dumps([i.model_dump(mode="json") for i in body.items],
                            separators=(",", ":"), ensure_ascii=False)
    if len(items_json.encode("utf-8")) > 16_384:
        raise HTTPException(413, detail="Pilot observation too large")
    record = BrandBrowserObservationModel(
        tenant_id=tenant, shop_id=shop_id, report_key=body.report_key,
        content_sha256=digest, platform=body.platform,
        shop_url_sha256=body.shop_url_sha256,
        status=body.status, observed_at=body.observed_at,
        items_json=items_json, imported_by=principal.actor_id,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Concurrent report key conflict; retry") from exc
    session.refresh(record)
    return {"item": safe_metadata(record), "replayed": False}


@router.get("/shops/{shop_id}/browser-observations")
def list_browser_observations(
    shop_id: str, response: Response,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    criteria = (
        BrandBrowserObservationModel.tenant_id == tenant,
        BrandBrowserObservationModel.shop_id == shop_id,
    )
    total = session.scalar(select(func.count()).select_from(
        BrandBrowserObservationModel).where(*criteria)) or 0
    rows = session.scalars(select(BrandBrowserObservationModel).where(*criteria)
        .order_by(BrandBrowserObservationModel.created_at.desc(),
                  BrandBrowserObservationModel.id.desc())
        .offset(offset).limit(limit)).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [safe_metadata(x) for x in rows],
        "count": total, "limit": limit, "offset": offset,
        "evidence_state": "browser_observation_unverified",
        "rights_verified": False, "scan_enabled": False, "dispatchable": False,
    }


@router.get("/shops/{shop_id}/browser-observations/{report_id}")
def read_browser_observation(
    shop_id: str, report_id: str, response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant = principal.active_tenant_id
    shop_for_tenant(session, tenant, shop_id)
    row = session.scalar(select(BrandBrowserObservationModel).where(
        BrandBrowserObservationModel.id == report_id,
        BrandBrowserObservationModel.tenant_id == tenant,
        BrandBrowserObservationModel.shop_id == shop_id,
    ))
    if row is None:
        raise HTTPException(404, detail="Browser pilot report not found")
    response.headers["Cache-Control"] = "no-store"
    return {
        **safe_metadata(row),
        "items": json.loads(row.items_json),
        "media_available": False,
        "source_verification": "unverified",
    }
