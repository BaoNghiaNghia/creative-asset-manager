"""CAM Muse Bridge API.

The Windows Scout token can push *metadata only* through the agent endpoint.
Muse has a distinct scoped credential; no login cookies, CodeLocal execution or
raw local filesystem paths are accepted. Video submission is a review receipt,
never proof of Google Drive ingestion or CAM listing linkage.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from sqlalchemy import select, update, func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.processing.model import ProcessingJobModel
from app.modules.etsy_spy.service import authenticate_agent
from app.modules.etsy_spy.image_agent import _private_drive_design, _checked_design_media
from .model import CamMuseJob

router = APIRouter(prefix="/api/v1/muse-bridge", tags=["muse-bridge"])
HEX64 = re.compile(r"^[a-f0-9]{64}$")
LISTING_ID = re.compile(r"^[0-9]{5,20}$")
ARTWORK_PATH = re.compile(
    r"^/api/v1/etsy-spy/image-agents/designs/[0-9a-f-]{36}/[0-9a-f]{24}/original$"
)
STATES = {"ready_for_muse", "qa_hold", "missing_source"}
VIDEO_HOSTS = {"drive.google.com", "docs.google.com"}

def _configured():
    token = os.environ.get("CAM_MUSE_BRIDGE_TOKEN", "")
    tenant = os.environ.get("CAM_MUSE_TENANT_ID", "")
    if len(token) < 48 or len(tenant) < 2:
        raise HTTPException(503, "Muse Bridge not configured")
    return token, tenant

def _muse(authorization: str | None = Header(default=None)):
    token, tenant = _configured()
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{48,128})", authorization or "")
    supplied = match.group(1) if match else ""
    if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),
                               hashlib.sha256(token.encode()).digest()):
        raise HTTPException(401, "Invalid Muse credential")
    return tenant

def _utc():
    return datetime.now(timezone.utc)

def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt

def _is_google_drive_url(value: str):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in VIDEO_HOSTS
                and parsed.username is None and parsed.password is None
                and parsed.port is None and len(value) <= 2048
                and parsed.path.startswith("/file/"))
    except ValueError:
        return False

class SyncItem(BaseModel):
    listingId: str
    pickedAt: datetime
    title: str = Field(min_length=5, max_length=700)
    keyword: str | None = Field(default=None, max_length=220)
    productImageUrl: str | None = Field(default=None, max_length=1024)
    artworkSha256: str | None = None
    artworkAssetPath: str | None = None
    status: str

    @field_validator("listingId")
    @classmethod
    def listing_id(cls, value):
        if not LISTING_ID.fullmatch(value):
            raise ValueError("invalid Etsy listing ID")
        return value

    @field_validator("status")
    @classmethod
    def source_status(cls, value):
        if value not in STATES:
            raise ValueError("invalid source state")
        return value

    @field_validator("artworkSha256")
    @classmethod
    def art_digest(cls, value):
        if value is not None and not HEX64.fullmatch(value):
            raise ValueError("invalid artwork digest")
        return value

    @field_validator("artworkAssetPath")
    @classmethod
    def art_asset(cls, value):
        if value is not None and not ARTWORK_PATH.fullmatch(value):
            raise ValueError("only CAM design artifact URLs allowed")
        return value

class SyncRequest(BaseModel):
    items: list[SyncItem] = Field(min_length=1, max_length=100)

class ClaimRequest(BaseModel):
    worker: str = Field(default="muse", max_length=80)

class VideoReceipt(BaseModel):
    aspectRatio: str
    url: str = Field(min_length=15, max_length=2048)
    sha256: str | None = None

class SubmitRequest(BaseModel):
    lease: str = Field(min_length=32, max_length=120)
    videos: list[VideoReceipt] = Field(min_length=2, max_length=2)

class VisualReviewVerdict(BaseModel):
    """Structured evidence from Muse's own QA; not a print or rights attestation."""
    verdict: Literal["pass", "fail"]
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceImageReviewed: bool
    designMatchesSource: bool
    originalTextPreserved: bool
    transparentBackgroundChecked: bool
    placementChecked: bool
    notes: str = Field(min_length=40, max_length=1200)
    reviewer: Literal["muse"] = "muse"
    confidence: float = Field(ge=0.0, le=1.0)


def _stored_native_asset(db: Session, tenant: str, listing_id: str, digest: str):
    """Match the exact Windows-approved SHA to an immutable CAM-stored Native Master."""
    if not HEX64.fullmatch(digest):
        return None
    jobs = db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.entity_type == "etsy_listing",
        ProcessingJobModel.entity_id == listing_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.cancellation_requested.is_(False),
        ProcessingJobModel.status == "completed",
    ).order_by(ProcessingJobModel.created_at.desc()).limit(16)).all()
    for job in jobs:
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        designs = payload.get("design_assets")
        if not isinstance(designs, list):
            continue
        for asset in designs:
            if (not isinstance(asset, dict)
                or asset.get("output_sha256") != digest
                or asset.get("artwork_kind") != "native_master_v2"
                or asset.get("position") != "front"
                or asset.get("method") != "imagegen"):
                continue
            aid = asset.get("id")
            if isinstance(aid, str) and re.fullmatch(r"[a-f0-9]{24}", aid):
                return (job.id, aid)
    return None


def _qa_record(row: CamMuseJob):
    r = row.output_receipts
    return r.get("visual_qa") if isinstance(r, dict) else None


def _row_view(job):
    return {"listingId": job.listing_key, "title": job.title,
            "keyword": job.keyword, "status": job.status,
            "pickedAt": job.picked_at, "artworkSha256": job.artwork_sha256,
            "artworkAssetPath": job.artwork_asset_path,
            "artworkUrl": f"/api/v1/muse-bridge/jobs/{job.listing_key}/artwork" if job.artwork_asset_path else None,
            "productImageUrl": job.product_image_url, "attempts": job.attempts}

@router.get("/health", include_in_schema=False)
def health():
    """Non-sensitive liveness probe for Muse connectors."""
    _configured()
    return {
        "ok": True, "service": "cam-muse-bridge", "version": "1.1.0",
        "authentication": "Bearer", "queuePath": "/api/v1/muse-bridge/queue",
        "videoGenerationEnabled": False,
    }


@router.get("/openapi.json", include_in_schema=False)
def scoped_openapi():
    """Publish only the Muse-facing contract, never internal CAM agent routes."""
    schema = get_openapi(
        title="CAM Muse Bridge", version="1.1.0",
        routes=router.routes,
        servers=[{"url": "https://creative-assets.ddns.net"}],
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["MuseBearer"] = {
        "type": "http", "scheme": "bearer",
    }
    schema["security"] = [{"MuseBearer": []}]
    # The internal Windows-agent sync route must never be published
    # as an action Muse can request.
    for uri in list(schema["paths"]):
        if "/agents/" in uri:
            del schema["paths"][uri]
    return schema


@router.post("/agents/{agent_id}/sync", include_in_schema=False)
def sync_agent(agent_id: str, payload: SyncRequest, authorization: str | None = Header(default=None),
               db: Session = Depends(get_db)):
    agent = authenticate_agent(db, agent_id, authorization)
    if len({x.listingId for x in payload.items}) != len(payload.items):
        raise HTTPException(422, "duplicate listing")
    tenant = agent.tenant_id
    picked = set(db.scalars(select(EtsySpyPicked.listing_key).where(
        EtsySpyPicked.tenant_id == tenant,
        EtsySpyPicked.listing_key.in_([i.listingId for i in payload.items]))).all())
    if len(picked) != len(payload.items):
        raise HTTPException(409, "Only tenant-owned Picked listings may sync")
    changed = 0
    for item in payload.items:
        row = db.get(CamMuseJob, (tenant, item.listingId))
        status = item.status
        # Don't treat unverified or inaccessible artwork as render-ready.
        if status == "ready_for_muse" and not (item.artworkSha256 and item.artworkAssetPath):
            status = "qa_hold"
        if row is None:
            row = CamMuseJob(tenant_id=tenant, listing_key=item.listingId,
                            picked_at=item.pickedAt, title=item.title,
                            source_state=status, status="pending" if status == "ready_for_muse" else status,
                            keyword=item.keyword, artwork_sha256=item.artworkSha256,
                            artwork_asset_path=item.artworkAssetPath,
                            product_image_url=item.productImageUrl)
            db.add(row)
            changed += 1
            continue
        if row.status in ("claimed", "submitted", "completed"):
            # Never overwrite a claimed or delivered job with stale source data.
            continue
        review = _qa_record(row)
        if (row.status == "pending" and isinstance(review, dict)
                and review.get("verdict") == "pass"
                and row.artwork_sha256 == item.artworkSha256
                and review.get("artwork_sha256") == item.artworkSha256
                and row.artwork_asset_path):
            # Windows proof files intentionally retain review_required=True.
            # A SHA-bound Muse visual QA pass remains valid across normal syncs.
            continue
        if isinstance(review, dict) and row.artwork_sha256 != item.artworkSha256:
            row.output_receipts = None
        row.picked_at = item.pickedAt
        row.title = item.title
        row.keyword = item.keyword
        row.product_image_url = item.productImageUrl
        row.artwork_sha256 = item.artworkSha256
        row.artwork_asset_path = item.artworkAssetPath
        row.source_state = status
        row.status = "pending" if status == "ready_for_muse" else status
        row.updated_at = _utc()
        changed += 1
    db.commit()
    return {"ok": True, "processed": len(payload.items), "updated": changed}

@router.get("/status")
def overview(tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    results = db.execute(select(CamMuseJob.status, func.count()).where(
        CamMuseJob.tenant_id == tenant).group_by(CamMuseJob.status)).all()
    return {"ok": True, "counts": {name: int(n) for name, n in results}}

@router.get("/jobs")
def pending_jobs(limit: int = Query(default=10, ge=1, le=50),
                 tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    query = select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.source_state == "ready_for_muse",
        or_(CamMuseJob.status == "pending",
            ((CamMuseJob.status == "claimed") & (CamMuseJob.lease_expires_at < _utc())))
    ).order_by(CamMuseJob.picked_at.asc()).limit(limit)
    return {"items": [_row_view(x) for x in db.scalars(query).all()]}

@router.get("/queue")
def muse_queue(limit: int = Query(default=10, ge=1, le=50),
               tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    """Compatibility alias for Muse background connector's /queue probe."""
    results = pending_jobs(limit=limit, tenant=tenant, db=db)
    return {"ok": True, "items": results["items"],
            "jobs": results["items"], "count": len(results["items"])}


@router.get("/reviews")
def visual_review_queue(
    listing_id: str | None = Query(default=None, pattern=r"^[0-9]{5,20}$"),
    limit: int = Query(default=10, ge=1, le=30),
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """SHA-matched artwork for Muse visual comparison; no ChatGPT is invoked."""
    query = select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.status == "qa_hold",
        CamMuseJob.artwork_sha256.is_not(None),
    )
    if listing_id:
        query = query.where(CamMuseJob.listing_key == listing_id)
    rows = db.scalars(query.order_by(CamMuseJob.picked_at.asc()).limit(
        1 if listing_id else 150)).all()
    items = []
    for row in rows:
        if len(items) >= limit:
            break
        if _qa_record(row):
            continue
        asset = _stored_native_asset(db, tenant, row.listing_key,
                                      row.artwork_sha256 or "")
        if asset is None:
            continue
        items.append({
            "listingId": row.listing_key, "title": row.title,
            "keyword": row.keyword, "artworkSha256": row.artwork_sha256,
            "sourceImageUrl": row.product_image_url,
            "artworkUrl": f"/api/v1/muse-bridge/reviews/{row.listing_key}/artwork",
            "videoGenerationEnabled": False,
            "requiresComparativeVisualQA": True,
            "rightsVerified": False,
            "printReady": False,
        })
    return {"ok": True, "items": items, "count": len(items),
            "canaryListingId": os.getenv("CAM_MUSE_QA_CANARY_LISTING_ID", "4456979256")}


@router.get("/reviews/{listing_id}/artwork")
async def review_artwork(
    listing_id: str, tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Private SHA-verified CAM PNG for Muse QA; not an asset approval."""
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    row = db.get(CamMuseJob, (tenant, listing_id))
    if not row or row.status != "qa_hold" or not row.artwork_sha256:
        raise HTTPException(404, "Artwork not in QA queue")
    found = _stored_native_asset(db, tenant, listing_id, row.artwork_sha256)
    if found is None:
        raise HTTPException(404, "Native Master SHA not found in CAM")
    job_id, asset_id = found
    remote = _private_drive_design(job_id, asset_id, tenant, db)
    headers = {"Cache-Control": "private, no-store",
               "X-Content-Type-Options": "nosniff",
               "Content-Disposition": 'inline; filename="muse-qa-native.png"'}
    if remote:
        from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
        if remote.get("output_sha256") != row.artwork_sha256:
            raise HTTPException(409, "Artwork SHA mismatch")
        db.rollback()
        png = await read_design_from_drive(
            tenant_id=tenant, asset_id=job_id+":"+asset_id+":original",
            remote_id=str(remote.get("drive_file_id") or ""),
            sha256=row.artwork_sha256, max_bytes=20*1024*1024,
        )
        return Response(png, media_type="image/png", headers=headers)
    source, digest = _checked_design_media(job_id, asset_id, db, tenant)
    if digest != row.artwork_sha256:
        raise HTTPException(409, "Artwork SHA mismatch")
    return FileResponse(str(source), media_type="image/png", headers=headers)


@router.post("/reviews/{listing_id}/verdict")
def submit_visual_review(
    listing_id: str, payload: VisualReviewVerdict,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Muse-only visual QA canary; preserves rights/print QA as unverified."""
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    canary = os.getenv("CAM_MUSE_QA_CANARY_LISTING_ID", "4456979256")
    if listing_id != canary:
        raise HTTPException(409, "Only first canary review may be promoted")
    row = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if row is None or row.status != "qa_hold" or _qa_record(row):
        raise HTTPException(409, "Review unavailable or already recorded")
    if row.artwork_sha256 != payload.artworkSha256:
        raise HTTPException(409, "Reviewed artwork SHA changed")
    found = _stored_native_asset(db, tenant, listing_id, payload.artworkSha256)
    if found is None:
        raise HTTPException(409, "CAM Native Master SHA not validated")
    has_checks = all((payload.sourceImageReviewed, payload.designMatchesSource,
                      payload.originalTextPreserved,
                      payload.transparentBackgroundChecked,
                      payload.placementChecked))
    if payload.verdict == "pass" and (not has_checks or payload.confidence < 0.95
                                       or not row.product_image_url):
        raise HTTPException(422, "Insufficient comparative visual QA evidence")
    review = {
        "verdict": payload.verdict, "reviewer": "muse",
        "artwork_sha256": payload.artworkSha256,
        "checks": {
            "sourceImageReviewed": payload.sourceImageReviewed,
            "designMatchesSource": payload.designMatchesSource,
            "originalTextPreserved": payload.originalTextPreserved,
            "transparentBackgroundChecked": payload.transparentBackgroundChecked,
            "placementChecked": payload.placementChecked,
        },
        "confidence": payload.confidence, "notes": payload.notes,
        "reviewed_at": _utc().isoformat(),
        "rights_verified": False, "print_ready": False,
    }
    row.output_receipts = {"visual_qa": review}
    if payload.verdict == "pass":
        job_id, asset_id = found
        row.artwork_asset_path = (
            f"/api/v1/etsy-spy/image-agents/designs/{job_id}/{asset_id}/original"
        )
        row.source_state = "ready_for_muse"
        row.status = "pending"
    row.updated_at = _utc()
    db.commit()
    return {"ok": True, "listingId": listing_id, "status": row.status,
            "visualQa": payload.verdict, "rightsVerified": False,
            "printReady": False, "videoGenerated": False}


@router.post("/jobs/{listing_id}/claim")
def claim(listing_id: str, payload: ClaimRequest,
          tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    lease = secrets.token_urlsafe(36)
    now = _utc()
    claimable = ((CamMuseJob.status == "pending") |
                 ((CamMuseJob.status == "claimed") & (CamMuseJob.lease_expires_at < now)))
    statement = update(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.listing_key == listing_id,
        CamMuseJob.source_state == "ready_for_muse", CamMuseJob.attempts < 5,
        claimable
    ).values(status="claimed", lease_hash=hashlib.sha256(lease.encode()).hexdigest(),
             lease_expires_at=now + timedelta(minutes=30),
             attempts=CamMuseJob.attempts + 1, updated_at=now)
    changed = db.execute(statement).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "Unavailable or artwork QA not passed")
    db.commit()
    row = db.get(CamMuseJob, (tenant, listing_id))
    return {"lease": lease, "leaseExpiresAt": row.lease_expires_at,
            "job": _row_view(row), "worker": payload.worker}

@router.get("/jobs/{listing_id}/artwork")
async def artwork(listing_id: str, tenant: str = Depends(_muse),
                  db: Session = Depends(get_db)):
    """Read the exact CAM-stored Native Master for a claimed Muse job.

    Never forwards a Scout credential, Drive secret, file path or browser cookie.
    Existing Drive SHA checks / tenant-scoped design ACLs remain authoritative.
    """
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    row = db.get(CamMuseJob, (tenant, listing_id))
    if (not row or row.status != "claimed"
            or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or row.source_state != "ready_for_muse"
            or not row.artwork_asset_path or not row.artwork_sha256):
        raise HTTPException(404, "Claimed artwork not available")
    parts = ARTWORK_PATH.fullmatch(row.artwork_asset_path)
    if parts is None:
        raise HTTPException(404, "Invalid artwork locator")
    job_id, asset_id = row.artwork_asset_path.split("/")[6:8]
    remote = _private_drive_design(job_id, asset_id, tenant, db)
    headers = {"Cache-Control": "private, no-store",
               "X-Content-Type-Options": "nosniff",
               "Content-Disposition": 'inline; filename="cam-native-artwork.png"'}
    if remote:
        from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
        if remote.get("output_sha256") != row.artwork_sha256:
            raise HTTPException(409, "Artwork SHA does not match approved source")
        db.rollback()
        png = await read_design_from_drive(
            tenant_id=tenant, asset_id=job_id+":"+asset_id+":original",
            remote_id=str(remote.get("drive_file_id") or ""),
            sha256=row.artwork_sha256, max_bytes=20*1024*1024,
        )
        return Response(png, media_type="image/png", headers=headers)
    source, digest = _checked_design_media(job_id, asset_id, db, tenant)
    if digest != row.artwork_sha256:
        raise HTTPException(409, "Artwork SHA does not match approved source")
    return FileResponse(str(source), media_type="image/png", headers=headers)


@router.post("/jobs/{listing_id}/submit")
def submit(listing_id: str, payload: SubmitRequest,
           tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    if {r.aspectRatio for r in payload.videos} != {"9:16", "16:9"}:
        raise HTTPException(422, "Requires one 9:16 and one 16:9 video")
    for r in payload.videos:
        if not _is_google_drive_url(r.url):
            raise HTTPException(422, "Only trusted Drive file URLs accepted")
        if r.sha256 is not None and not HEX64.fullmatch(r.sha256):
            raise HTTPException(422, "Invalid SHA-256")
    row = db.get(CamMuseJob, (tenant, listing_id))
    if not row or row.status != "claimed" or not row.lease_hash:
        raise HTTPException(409, "Job not claimed")
    if _aware(row.lease_expires_at) <= _utc():
        raise HTTPException(409, "Lease expired")
    if not hmac.compare_digest(row.lease_hash,
                               hashlib.sha256(payload.lease.encode()).hexdigest()):
        raise HTTPException(409, "Invalid lease")
    row.status = "submitted"
    row.lease_hash = None
    row.lease_expires_at = None
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    receipts["videos"] = [r.model_dump() for r in payload.videos]
    row.output_receipts = receipts
    row.submitted_at = _utc()
    row.updated_at = _utc()
    db.commit()
    return {"ok": True, "status": "submitted", "camLinked": False,
            "message": "Video receipts await content QA and CAM ingestion"}
