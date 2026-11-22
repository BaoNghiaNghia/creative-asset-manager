"""CAM Muse Bridge API.

The Windows Scout token can push *metadata only* through the agent endpoint.
Muse has a distinct scoped credential; no login cookies, CodeLocal execution or
raw local filesystem paths are accepted. Video submission is a review receipt,
never proof of Google Drive ingestion or CAM listing linkage.
"""
from __future__ import annotations

import hashlib
import json
import ipaddress
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from sqlalchemy import delete, select, update, func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.processing.model import ProcessingJobModel
from app.modules.etsy_spy.service import authenticate_agent
from app.modules.etsy_spy.image_agent import _private_drive_design, _checked_design_media
from .model import CamMuseJob, CamMuseCommand

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


def _motion_v32_pilot_spec():
    """Opt-in pilot override. V3.1 remains the default for non-pilot work.

    The existing Muse task chooses the three distinct new listings; CAM does
    not auto-promote the pilot or re-render already stored videos.
    """
    return {
        "motionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_2",
        "rolloutMode": "pilot_only",
        "pilotSizeNewListings": 3,
        "pilotSelectionManagedBy": "existing CAM Auto Video scheduled task in Muse",
        "afterThirdListing": "report motion QA and return to V3.1 until user explicitly approves rollout",
        "inheritV31StartFrameAndEyeLightingRules": True,
        "videoDurationSeconds": 12,
        "shotStructure": "ONE continuous take; 0-3s begin the action, 3-8s complete and settle, 8-12s comfortable non-frozen posture; guide only, not animation keyframes",
        "motionPrompt": (
            "Documentary-looking authentic handheld phone UGC of a real person, "
            "never an animated still photo. Perform ONE motivated, ordinary "
            "action grounded in the source pose and environment, such as "
            "taking two casual steps, shifting weight near a window, leaning "
            "lightly against a wall, adjusting a seated position, or briefly "
            "looking toward something in the scene. The primary action lasts "
            "about 2-4 seconds at normal human speed. Keep believable incidental "
            "movement throughout the remaining time: breathing, slight "
            "posture and weight adjustments, relaxed shoulders, occasional "
            "brief glances, natural blinking, and cotton folds moving IN SYNC "
            "with the body. No motionless person while snow, leaves or other "
            "background effects move. Do not force constant head bobbing or "
            "repetitive swaying as fake motion. Keep the front print completely "
            "visible, undistorted, and at the exact approved physical scale."
        ),
        "camera": "single steady human handheld phone shot, tiny believable micro-movement, no hard cuts, dramatic zooms or forced slow motion",
        "environmentCheck": (
            "Clothing, weather, behavior and location must make physical sense. "
            "For a short-sleeve Comfort Colors C1717, prefer a porch, cafe, "
            "garden, window-lit room or mild-weather street. Do not place an "
            "uncovered T-shirt wearer in heavy falling snow without a credible "
            "reason. Never invent props or alter the approved artwork."
        ),
        "motionQaReject": [
            "subject nearly frozen or animated like a still photograph",
            "background moving while person's body stays unnaturally static",
            "cotton fabric moving independently of torso and arms",
            "physically implausible weather, clothing or behavior",
            "unrealistic hand, face, shoulder or neck mechanics",
            "repeated theatrical nodding, rocking, dancing or forced facial acting",
            "hard scene cuts, abrupt camera movement or slow-motion performance",
            "cropped, obscured, re-lettered or distorted front print",
        ],
        "qaMethod": (
            "Watch the whole video for human motion and clothing realism, "
            "then inspect early, middle and late frames. QA is never implied "
            "merely by a stored MP4 or an asserted motion-version header."
        ),
        "preserveNativeMaster": True,
        "designTopBelowCollarCm": 10,
        "formats": ["9:16", "16:9"],
        "completedVideoPolicy": "do_not_regenerate_without_user_approval",
        "motionVersionUploadHeader": "X-CAM-Motion-Version",
        "noOpenAiApi": True,
    }


def _row_view(job):
    return {"listingId": job.listing_key, "title": job.title,
            "keyword": job.keyword, "status": job.status,
            "pickedAt": job.picked_at, "artworkSha256": job.artwork_sha256,
            "artworkAssetPath": job.artwork_asset_path,
            "artworkUrl": f"/api/v1/muse-bridge/jobs/{job.listing_key}/artwork" if job.artwork_asset_path else None,
            "productImageUrl": job.product_image_url, "attempts": job.attempts,
            "videoSpec": {
                "formats": ["9:16", "16:9"], "targetDurationSeconds": 12,
                "garment": "Comfort Colors C1717", "printPosition": "front_center",
                "designTopBelowCollarCm": 10,
                "artworkShaStrict": True,
                "motionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_1",
                "pilotMotionSpecV32": _motion_v32_pilot_spec(),
                "motionVersionUploadHeader": "X-CAM-Motion-Version",
                "motionStyle": "unposed candid documentary footage; real-time human behavior, not fashion acting or a slow-motion portrait",
                "startFramePolicy": (
                    "PRE-RENDER LIGHTING CHECK: Inspect the face, sun angle, "
                    "eye direction and starting expression. Avoid facing the "
                    "subject into strong direct sunlight: prefer open shade, "
                    "diffuse side-light or gentle backlight with a relaxed face. "
                    "If no suitable natural face-visible frame exists, crop "
                    "from below the mouth to mid-thigh and keep the complete "
                    "shirt artwork visible. NEVER force eyes open in harsh sun, "
                    "morph a squint into an artificial stare, add sunshades or "
                    "invent new props. If source integrity/renderer restrictions "
                    "prevent a safe alternate image or crop, report blocked "
                    "without rendering the same bad starting frame again."
                ),
                "motionPrompt": (
                    "One continuous 12-second candid UGC take. The subject is "
                    "relaxed, breathing naturally, and not posing for a camera. "
                    "Choose ONE concrete everyday action grounded in the starting "
                    "image and location: shift weight once by the garden fence, "
                    "take 1-2 unhurried steps if already standing freely, "
                    "or slightly adjust a seated position if already sitting. "
                    "The action has a reason and takes a believable 2-4 seconds "
                    "at real-world speed; do not manufacture a slow-motion pose. "
                    "Then the subject settles naturally with subtle breathing, "
                    "brief unforced blinks, relaxed facial muscles and an "
                    "occasional small glance. Eye behavior MUST match lighting: "
                    "slight ordinary squinting is acceptable in bright daylight, "
                    "but sustained forced closing or staring is not. Prefer "
                    "a print-focused crop if facial animation distracts. "
                    "The camera feels like "
                    "an observer holding a phone steadily at chest height, with "
                    "barely perceptible human micro-sway. Maintain a mostly "
                    "front-facing torso, authentic cotton drape and a fully "
                    "visible, stable front print. Preserve original scene objects; "
                    "do not invent a coffee cup or other prop."
                ),
                "motionQaReject": [
                    "face aimed into harsh direct sunlight with no natural shade or crop alternative",
                    "forced wide-eyed stare or unnaturally fixed smile in bright sun",
                    "sustained eyelid closure inconsistent with the scene and human reaction",
                    "repeated slow head tilts, head rolls, nodding loops or robotic oscillation",
                    "fixed smiling expression for most of the clip",
                    "slow-motion or exaggerated acting instead of a motivated everyday action",
                    "unmotivated hand gestures or hands obscuring the front print",
                    "unrealistic hand, face, neck, fabric or artwork deformation",
                    "sudden camera jumps, hard scene cuts or looped body gestures",
                ],
                "camera": "single observational take; stable chest-to-hip print-focused medium framing, minimal natural sway",
                "lighting": "prefer open shade, diffuse side-light or gentle backlight; avoid glare into subject's eyes",
                "renderRetryPolicy": {
                    "maxTriesPerAspectPerStartingFrame": 1,
                    "newStartingFrameRequiredAfterEyeOrLightingFailure": True,
                    "blockedByVideoTool": "stop and report; do not bypass or repeatedly retry an integrity refusal",
                    "artworkOrPrintCropFailure": "reject; reframe with fully visible original print and correct physical scale",
                    "noFakeOutputs": True,
                },
                "preserveArtworkScaleAcrossShots": True,
                "noOpenAiApi": True,
                "videoUploadPaths": {
                    "9:16": f"/api/v1/muse-bridge/jobs/{job.listing_key}/videos/vertical",
                    "16:9": f"/api/v1/muse-bridge/jobs/{job.listing_key}/videos/horizontal",
                },
                "uploadContentType": "video/mp4",
                "uploadLeaseHeader": "X-Muse-Lease",
            },
        }

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


def _verified_video_ratios(job: CamMuseJob) -> set[str]:
    """Count only SHA-bound, CAM-verified files in the listing/design folder."""
    receipt = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    uploads = receipt.get("drive_uploads")
    if not isinstance(uploads, dict) or not job.artwork_sha256:
        return set()
    folder = (
        f"listing_{job.listing_key}/design_{job.artwork_sha256[:16]}_front/video_ugc"
    )
    ratios = set()
    for ratio in ("9:16", "16:9"):
        item = uploads.get(ratio)
        if (isinstance(item, dict)
                and item.get("storageVerified") is True
                and item.get("storage_provider") == "google_drive_managed"
                and item.get("relativeFolder") == folder
                and isinstance(item.get("drive_file_id"), str)
                and isinstance(item.get("drive_folder_id"), str)
                and isinstance(item.get("sha256"), str)
                and HEX64.fullmatch(item["sha256"])):
            ratios.add(ratio)
    return ratios


@router.get("/video-work")
def missing_video_work(
    limit: int = Query(default=10, ge=1, le=20),
    tenant: str = Depends(_muse),
    db: Session = Depends(get_db),
):
    """One read-only Muse GET: discover queued listings missing one/both videos.

    Never generates a video, approves artwork, leases a job, or requeues rows.
    Muse must authenticate, claim an eligible command and complete real work.
    """
    inventory_total = int(db.scalar(select(func.count()).select_from(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
    )) or 0)
    all_jobs = db.scalars(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
    ).order_by(CamMuseJob.picked_at.asc()).limit(1000)).all()
    by_listing = {job.listing_key: job for job in all_jobs}
    remaining = sum(
        1 for job in all_jobs
        if job.source_state != "missing_source"
        and len(_verified_video_ratios(job)) < 2
    )
    candidates = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.action.in_(("create_video", "review_artwork")),
        CamMuseCommand.status.in_(("pending", "claimed")),
        CamMuseCommand.attempts < 5,
    ).order_by(CamMuseCommand.created_at.asc()).limit(100)).all()

    items = []
    seen = set()
    for cmd in candidates:
        if cmd.listing_key in seen:
            continue
        if (cmd.status == "claimed"
                and (cmd.lease_expires_at is None
                     or _aware(cmd.lease_expires_at) > _utc())):
            continue
        job = by_listing.get(cmd.listing_key)
        if not job or job.artwork_sha256 != cmd.artwork_sha256:
            continue
        saved = _verified_video_ratios(job)
        if len(saved) == 2:
            continue
        phase = _eligible_phase(db, tenant, job, cmd)
        if phase is None:
            continue
        seen.add(job.listing_key)
        missing = [x for x in ("9:16", "16:9") if x not in saved]
        lid = job.listing_key
        items.append({
            "commandId": cmd.command_id,
            "listingId": lid,
            "title": job.title,
            "artworkSha256": job.artwork_sha256,
            "nextStep": phase,
            "missingAspectRatios": missing,
            "savedAspectRatios": [x for x in ("9:16", "16:9") if x in saved],
            "attempts": cmd.attempts,
            "claimCommandPath": f"/api/v1/muse-bridge/commands/{cmd.command_id}/claim",
            "heartbeatCommandPath": f"/api/v1/muse-bridge/commands/{cmd.command_id}/heartbeat",
            "completeCommandPath": f"/api/v1/muse-bridge/commands/{cmd.command_id}/complete",
            "reviewPath": f"/api/v1/muse-bridge/reviews?listing_id={lid}"
                          if phase == "review_artwork" else None,
            "claimVideoJobPath": f"/api/v1/muse-bridge/jobs/{lid}/claim"
                                 if phase == "create_video" else None,
            "artworkPath": f"/api/v1/muse-bridge/jobs/{lid}/artwork"
                           if phase == "create_video" else None,
            "uploadPaths": {
                "9:16": f"/api/v1/muse-bridge/jobs/{lid}/videos/vertical",
                "16:9": f"/api/v1/muse-bridge/jobs/{lid}/videos/horizontal",
            } if phase == "create_video" else None,
        })
    items.sort(key=lambda row: (
        row["nextStep"] != "create_video", row["attempts"], row["listingId"]
    ))
    items = items[:limit]
    return {
        "ok": True, "items": items, "count": len(items),
        "remainingWithoutBothVideos": remaining,
        "inventoryTotal": inventory_total,
        "inventoryScanned": len(all_jobs),
        "remainingCountIsPartial": inventory_total > len(all_jobs),
        "pollAfterSeconds": 300,
        "requiresMuseWorker": True,
        "needsComparativeArtworkQA": True,
        "motionPolicy": {
            "defaultMotionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_1",
            "pilotMotionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_2",
            "pilotSizeNewListings": 3,
            "pilotManagedBy": "existing CAM Auto Video scheduled task in Muse",
            "afterPilot": "default V3.1 until user approves expansion",
            "pilotSpecInJob": "videoSpec.pilotMotionSpecV32",
            "motionVersionUploadHeader": "X-CAM-Motion-Version",
        },
        "outputDestination": "CAM managed private Google Drive listing/design/video_ugc",
    }


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
        1 if listing_id else 500)).all()
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
            "visualQaRequired": True, "rightsVerified": False}


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
    # Preview-only batch progression: each listing still requires its own
    # source-vs-artwork visual QA, exact Native SHA, and recorded evidence.
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
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    if receipts.get("drive_uploads"):
        raise HTTPException(409, "Do not overwrite CAM-verified video uploads with external receipts")
    row.status = "submitted"
    row.lease_hash = None
    row.lease_expires_at = None
    receipts["videos"] = [r.model_dump() for r in payload.videos]
    row.output_receipts = receipts
    row.submitted_at = _utc()
    row.updated_at = _utc()
    db.commit()
    return {"ok": True, "status": "submitted", "camLinked": False,
            "message": "Video receipts await content QA and CAM ingestion"}

@router.post(
    "/jobs/{listing_id}/videos/{aspect}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"video/mp4": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
async def upload_ugc_video(
    listing_id: str, aspect: Literal["vertical", "horizontal"],
    request: Request, x_muse_lease: str | None = Header(default=None),
    x_cam_motion_version: Literal[
        "CAM_NATURAL_EVERYDAY_MOTION_V3_1",
        "CAM_NATURAL_EVERYDAY_MOTION_V3_2",
    ] | None = Header(default=None),
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Muse uploads the actual MP4; CAM verifies and saves to listing/design Drive.

    This is separate from the legacy receipt-only submit endpoint. It does
    not call any AI model, and bytes are held in bounded memory, never saved
    as persistent files on the VPS. Video content QA remains pending.
    """
    from .video_storage import MAX_VIDEO_BYTES, store_muse_video
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    lease = x_muse_lease or ""
    if not 32 <= len(lease) <= 120 or request.headers.get("content-type", "").split(";")[0].lower() != "video/mp4":
        raise HTTPException(422, "Muse lease and video/mp4 required")
    row = db.get(CamMuseJob, (tenant, listing_id))
    if (row is None or row.status != "claimed" or row.source_state != "ready_for_muse"
            or not row.artwork_sha256 or not row.lease_hash
            or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or not hmac.compare_digest(row.lease_hash, hashlib.sha256(lease.encode()).hexdigest())):
        raise HTTPException(409, "Muse job not claimable or video lease invalid")
    parts = bytearray()
    async for chunk in request.stream():
        parts.extend(chunk)
        if len(parts) > MAX_VIDEO_BYTES:
            raise HTTPException(413, "MP4 exceeds 40 MiB limit")
    if len(parts) < 2048:
        raise HTTPException(422, "Missing MP4")
    ratio = "9:16" if aspect == "vertical" else "16:9"
    digest = hashlib.sha256(parts).hexdigest()
    existing_receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
    previous = (existing_receipts.get("drive_uploads") or {}).get(ratio)
    if isinstance(previous, dict):
        if previous.get("sha256") != digest:
            raise HTTPException(409, "An alternate video for this aspect was already saved")
        return {"ok": True, "reused": True, "status": row.status,
                "aspectRatio": ratio, "storageVerified": True,
                "motionPromptVersion": previous.get("motionPromptVersion"),
                "driveFolder": previous.get("relativeFolder")}
    expected_artwork_sha = row.artwork_sha256
    stored = await store_muse_video(
        tenant_id=tenant, listing_id=listing_id,
        artwork_sha256=expected_artwork_sha, aspect=aspect, mp4=bytes(parts),
    )
    if x_cam_motion_version is not None:
        # Muse-declared generation version, NOT an independent content-QA pass.
        stored["motionPromptVersion"] = x_cam_motion_version
        stored["motionVersionSource"] = "muse_declared"
    # A second upload might have been received while Google Drive was saving:
    # validate the exact lease and content hash after obtaining a row lock.
    db.expire_all()
    locked = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if (locked is None or locked.status != "claimed" or
            locked.artwork_sha256 != expected_artwork_sha or
            not locked.lease_hash or
            _aware(locked.lease_expires_at) is None or
            _aware(locked.lease_expires_at) <= _utc() or
            not hmac.compare_digest(locked.lease_hash,
                hashlib.sha256(lease.encode()).hexdigest())):
        db.rollback()
        raise HTTPException(409, "Muse lease changed during upload")
    receipts = dict(locked.output_receipts) if isinstance(locked.output_receipts, dict) else {}
    uploads = dict(receipts.get("drive_uploads") or {})
    if ratio in uploads and uploads[ratio].get("sha256") != digest:
        db.rollback()
        raise HTTPException(409, "Conflicting video variant")
    uploads[ratio] = stored
    receipts["drive_uploads"] = uploads
    if set(uploads) == {"9:16", "16:9"}:
        # Storage verified != video content approved. Listing UI can preview it,
        # but must flag both variants as pending video content review.
        receipts["videos"] = [uploads["9:16"], uploads["16:9"]]
        receipts["camLinked"] = True
        receipts["videoContentQa"] = "pending_review"
        locked.status = "submitted"
        locked.submitted_at = _utc()
        locked.lease_hash = None
        locked.lease_expires_at = None
    locked.output_receipts = receipts
    locked.updated_at = _utc()
    db.commit()
    return {"ok": True, "reused": False, "status": locked.status,
            "aspectRatio": ratio, "storedCount": len(uploads),
            "storageVerified": True, "driveFolder": stored["relativeFolder"],
            "camLinked": bool(receipts.get("camLinked", False)),
            "videoContentQa": "pending_review",
            "motionPromptVersion": stored.get("motionPromptVersion")}


# ---- Limited ChatGPT / CodeLocal -> CAM -> Muse command handoff ----
# This API does NOT execute models or machine commands. Only two predeclared
# workflow intents are supported, and Muse must explicitly poll /commands.

def _operator(authorization: str | None = Header(default=None)):
    _, tenant = _configured()
    secret = os.getenv("CAM_MUSE_COMMAND_TOKEN", "")
    if len(secret) < 48:
        raise HTTPException(503, "CAM Muse command submission not configured")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{48,128})", authorization or "")
    candidate = match.group(1) if match else ""
    if not hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(),
                               hashlib.sha256(secret.encode()).digest()):
        raise HTTPException(401, "Unauthorized operator")
    return tenant


def _command_reader(authorization: str | None = Header(default=None)):
    muse_token, tenant = _configured()
    op_token = os.getenv("CAM_MUSE_COMMAND_TOKEN", "")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{48,128})", authorization or "")
    candidate = match.group(1) if match else ""
    digest = hashlib.sha256(candidate.encode()).digest()
    muse_ok = hmac.compare_digest(digest, hashlib.sha256(muse_token.encode()).digest())
    op_ok = len(op_token) >= 48 and hmac.compare_digest(
        digest, hashlib.sha256(op_token.encode()).digest()
    )
    if not (muse_ok or op_ok):
        raise HTTPException(401, "Unauthorized")
    return tenant


class CommandCreate(BaseModel):
    action: Literal["create_video", "review_artwork", "execute_task"]
    listingId: str | None = Field(default=None, pattern=r"^[0-9]{5,20}$")
    taskType: str | None = Field(default=None, min_length=5, max_length=80,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}$")
    instructions: str | None = Field(default=None, min_length=15, max_length=2000)
    requestId: str = Field(min_length=12, max_length=128,
                           pattern=r"^[A-Za-z0-9_-]+$")
    note: str | None = Field(default=None, max_length=300)


class TaskLink(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    url: str = Field(min_length=12, max_length=600)
    imageUrl: str | None = Field(default=None, max_length=600)
    description: str | None = Field(default=None, max_length=300)


class TaskResult(BaseModel):
    summary: str = Field(min_length=10, max_length=2000)
    links: list[TaskLink] = Field(default_factory=list, max_length=30)


class CommandHeartbeat(BaseModel):
    lease: str = Field(min_length=32, max_length=128)
    state: Literal["started", "researching", "collecting", "validating", "uploading", "blocked"]
    note: str | None = Field(default=None, max_length=180)


class CommandAck(BaseModel):
    lease: str = Field(min_length=32, max_length=128)
    outcome: Literal["success", "failed"]
    notes: str = Field(min_length=10, max_length=800)
    result: TaskResult | None = None


def _safe_external_link(value: str) -> bool:
    try:
        loc = urlsplit(value)
        host = loc.hostname or ""
        if not host or loc.scheme != "https" or loc.username or loc.password or loc.port:
            return False
        if host.lower() in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True
    except ValueError:
        return False


def _valid_task_result(task_type: str, result: TaskResult) -> bool:
    links = result.links
    if not all(_safe_external_link(ref.url) and
               (ref.imageUrl is None or _safe_external_link(ref.imageUrl))
               for ref in links):
        return False
    if task_type == "research.pinterest_poses":
        pins = set()
        for ref in links:
            try:
                pin = urlsplit(ref.url)
                photo = urlsplit(ref.imageUrl or "")
            except ValueError:
                return False
            if (pin.hostname not in {"www.pinterest.com", "pinterest.com"}
                    or not re.fullmatch(r"/pin/[0-9]{8,20}/?", pin.path)
                    or pin.query or pin.fragment or photo.hostname != "i.pinimg.com"
                    or not photo.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
                return False
            pins.add(pin.path.strip("/"))
        return len(links) == 20 and len(pins) == 20
    return True


def _purge_old_generic_tasks(db: Session, tenant: str):
    """Bounded cleanup. Keep legacy garment QA/video audit trails untouched."""
    db.execute(update(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.action == "execute_task",
        CamMuseCommand.status == "claimed",
        CamMuseCommand.attempts >= 5,
        CamMuseCommand.lease_expires_at < _utc(),
    ).values(status="failed", finished_at=_utc(),
             lease_hash=None, lease_expires_at=None, updated_at=_utc()))
    expired = db.scalars(select(CamMuseCommand.command_id).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.action == "execute_task",
        CamMuseCommand.status.in_(("completed", "failed")),
        CamMuseCommand.finished_at < _utc() - timedelta(days=30),
    ).limit(100)).all()
    if expired:
        db.execute(delete(CamMuseCommand).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.command_id.in_(expired),
        ))


def _command_status(cmd: CamMuseCommand):
    return {
        "commandId": cmd.command_id, "listingId": cmd.listing_key,
        "action": cmd.action, "status": cmd.status, "phase": cmd.phase,
        "artworkSha256": cmd.artwork_sha256, "attempts": cmd.attempts,
        "createdAt": cmd.created_at.isoformat() if cmd.created_at else None,
        "updatedAt": cmd.updated_at.isoformat() if cmd.updated_at else None,
        "finishedAt": cmd.finished_at.isoformat() if cmd.finished_at else None,
        "leaseExpiresAt": cmd.lease_expires_at.isoformat() if cmd.lease_expires_at else None,
        "leaseExpired": (cmd.status == "claimed" and cmd.lease_expires_at is not None
                         and _aware(cmd.lease_expires_at) <= _utc()),
        "lastHeartbeatAt": ((cmd.details or {}).get("lastHeartbeatAt")
                            if isinstance(cmd.details, dict) else None),
        "progressState": ((cmd.details or {}).get("progressState")
                          if isinstance(cmd.details, dict) else None),
        "camLinked": False,
    }


def _eligible_phase(db: Session, tenant: str,
                    job: CamMuseJob, cmd: CamMuseCommand):
    if job.artwork_sha256 != cmd.artwork_sha256:
        return None
    # Each SHA-matched candidate may now be independently reviewed by Muse.
    # The verdict endpoint still demands real comparative visual evidence.
    if cmd.action == "review_artwork":
        if job.status != "qa_hold":
            return None
        return ("review_artwork" if job.product_image_url and _stored_native_asset(
            db, tenant, job.listing_key, cmd.artwork_sha256
        ) else None)
    if job.status == "qa_hold":
        return ("review_artwork" if _stored_native_asset(
            db, tenant, job.listing_key, cmd.artwork_sha256
        ) and job.product_image_url else None)
    claimable = (job.status == "pending" or
                 (job.status == "claimed"
                  and _aware(job.lease_expires_at) is not None
                  and _aware(job.lease_expires_at) <= _utc()))
    if (job.source_state == "ready_for_muse"
            and claimable and job.attempts < 5
            and job.artwork_asset_path
            and isinstance(_qa_record(job), dict)
            and _qa_record(job).get("verdict") == "pass"
            and _qa_record(job).get("artwork_sha256") == cmd.artwork_sha256):
        return "create_video"
    return None


@router.post("/commands", include_in_schema=False, status_code=202)
def create_operator_command(payload: CommandCreate,
                            tenant: str = Depends(_operator),
                            db: Session = Depends(get_db)):
    """CodeLocal operator only: typed job intent; no free-form machine control."""
    if payload.action == "execute_task":
        if payload.listingId is not None or not payload.taskType or not payload.instructions:
            raise HTTPException(422, "Generic tasks require taskType/instructions and no listingId")
        group = payload.taskType.split(".", 1)[0]
        if group not in {
            "research", "analysis", "creative", "content", "design",
            "media", "report", "document", "image", "video", "qa", "collect"
        }:
            raise HTTPException(422, "Muse generic tasks are non-destructive content/research only")
        _purge_old_generic_tasks(db, tenant)
        prior = db.scalar(select(CamMuseCommand).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.request_id == payload.requestId,
        ).limit(1))
        if prior:
            if (prior.action != "execute_task"
                    or not isinstance(prior.details, dict)
                    or prior.details.get("taskType") != payload.taskType
                    or prior.details.get("instructions") != payload.instructions):
                raise HTTPException(409, "requestId belongs to another job or task content changed")
            return {"ok": True, "reused": True, **_command_status(prior)}
        count = db.scalar(select(func.count()).select_from(CamMuseCommand).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.action == "execute_task",
            CamMuseCommand.status.in_(("pending", "claimed")),
        ))
        if (count or 0) >= 50:
            raise HTTPException(429, "Generic Muse queue capacity reached")
        cmd = CamMuseCommand(
            tenant_id=tenant, command_id=str(uuid4()),
            listing_key=None, artwork_sha256=None,
            action="execute_task", request_id=payload.requestId,
            status="pending", phase="execute_task",
            details={
                "taskType": payload.taskType,
                "instructions": payload.instructions,
                "note": payload.note or "",
                "executor": "muse",
                "openaiApiAllowed": False,
                "createdVia": "codelocal",
            },
        )
        db.add(cmd)
        db.commit()
        return {"ok": True, "reused": False, **_command_status(cmd)}
    if payload.listingId is None:
        raise HTTPException(422, "Etsy listing ID is required for garment jobs")
    job = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == payload.listingId
    ).with_for_update())
    if not job:
        raise HTTPException(404, "No matching Picked CAM job")
    prior = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.request_id == payload.requestId
    ).limit(1))
    if prior:
        if prior.listing_key != payload.listingId or prior.action != payload.action:
            raise HTTPException(409, "requestId reused for a different task")
        return {"ok": True, "reused": True, **_command_status(prior)}
    if (job.status in ("submitted", "completed")
            or (job.status == "claimed"
                and (_aware(job.lease_expires_at) is None
                     or _aware(job.lease_expires_at) > _utc()))
            or job.source_state == "missing_source"):
        raise HTTPException(409, "Job already submitted or original source unavailable")
    if not job.artwork_sha256 or not HEX64.fullmatch(job.artwork_sha256):
        raise HTTPException(409, "No verified artwork digest")
    candidate = CamMuseCommand(
        tenant_id=tenant, command_id=str(uuid4()),
        listing_key=job.listing_key, action=payload.action,
        request_id=payload.requestId,
        artwork_sha256=job.artwork_sha256,
        status="pending",
        phase="review_artwork",
        details={"origin": "codelocal-operator",
                 "note": payload.note or "", "rights_verified": False},
    )
    phase = _eligible_phase(db, tenant, job, candidate)
    if not phase:
        raise HTTPException(409, "Listing is not available for this workflow")
    active = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.listing_key == payload.listingId,
        CamMuseCommand.status.in_(("pending", "claimed")),
    ).order_by(CamMuseCommand.created_at.desc()).limit(1))
    if active:
        return {"ok": True, "reused": True, **_command_status(active)}
    candidate.phase = phase
    db.add(candidate)
    db.commit()
    return {"ok": True, "reused": False, **_command_status(candidate)}


@router.get("/commands")
def list_muse_commands(limit: int = Query(default=10, ge=1, le=20),
                       tenant: str = Depends(_muse),
                       db: Session = Depends(get_db)):
    """Muse polls this endpoint; commands are never pushed into a chat session."""
    rows = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.status.in_(("pending", "claimed")),
        CamMuseCommand.attempts < 5,
    ).order_by(CamMuseCommand.created_at.asc()).limit(100)).all()
    items = []
    for cmd in rows:
        if len(items) >= limit:
            break
        if (cmd.status == "claimed" and cmd.lease_expires_at is not None
                and _aware(cmd.lease_expires_at) > _utc()):
            continue
        if cmd.action == "execute_task":
            d = cmd.details if isinstance(cmd.details, dict) else {}
            items.append({
                **_command_status(cmd),
                "phase": "execute_task",
                "task": {
                    "taskType": d.get("taskType"),
                    "instructions": d.get("instructions"),
                    "executor": "muse",
                    "openaiApiAllowed": False,
                    "resultContract": "summary plus max 30 HTTPS links (title/url/imageUrl/description)",
                },
            })
            continue
        job = db.get(CamMuseJob, (tenant, cmd.listing_key))
        if job is None:
            continue
        phase = _eligible_phase(db, tenant, job, cmd)
        if phase is None:
            continue
        items.append({**_command_status(cmd), "phase": phase,
                      "title": job.title,
                      "reviewPath": f"/api/v1/muse-bridge/reviews?listing_id={cmd.listing_key}",
                      "jobsPath": "/api/v1/muse-bridge/jobs",
                      "videoGenerationEnabled": phase == "create_video",
                      "videoExecutor": "muse",
                      "camInternalVideoGenerationEnabled": False,
                      "requiresMusePolling": True})
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/commands/{command_id}/status", include_in_schema=False)
def read_operator_command(command_id: str, tenant: str = Depends(_command_reader),
                          db: Session = Depends(get_db)):
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid command ID")
    cmd = db.get(CamMuseCommand, (tenant, command_id))
    if not cmd:
        raise HTTPException(404, "Unknown command")
    response = {"ok": True, **_command_status(cmd)}
    if cmd.action == "execute_task" and isinstance(cmd.details, dict):
        response["taskType"] = cmd.details.get("taskType")
        if cmd.status in ("completed", "failed"):
            response["result"] = cmd.details.get("result")
            response["completionNotes"] = cmd.details.get("notes")
    return response


@router.post("/commands/{command_id}/claim")
def claim_muse_command(command_id: str, tenant: str = Depends(_muse),
                       db: Session = Depends(get_db)):
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid command ID")
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if cmd is None or cmd.status not in ("pending", "claimed") or cmd.attempts >= 5:
        raise HTTPException(409, "Command not available")
    if (cmd.status == "claimed" and cmd.lease_expires_at is not None
            and _aware(cmd.lease_expires_at) > _utc()):
        raise HTTPException(409, "Command already claimed")
    if cmd.action == "execute_task":
        phase = "execute_task"
    else:
        job = db.get(CamMuseJob, (tenant, cmd.listing_key))
        if not job:
            raise HTTPException(409, "CAM listing no longer Picked")
        phase = _eligible_phase(db, tenant, job, cmd)
        if phase is None:
            raise HTTPException(409, "Artwork QA / video workflow not eligible")
    lease = secrets.token_urlsafe(36)
    cmd.phase = phase
    cmd.lease_hash = hashlib.sha256(lease.encode()).hexdigest()
    cmd.lease_expires_at = _utc() + timedelta(minutes=30)
    cmd.status = "claimed"
    cmd.attempts += 1
    cmd.updated_at = _utc()
    db.commit()
    return {"ok": True, "command": _command_status(cmd),
            "lease": lease, "leaseExpiresAt": cmd.lease_expires_at,
            "nextStep": "execute_task" if phase == "execute_task" else
                        "reviews" if phase == "review_artwork" else "jobs",
            "task": (cmd.details if cmd.action == "execute_task" else None),
            "artworkSha256": cmd.artwork_sha256}


@router.post("/commands/{command_id}/heartbeat")
def heartbeat_muse_command(
    command_id: str, payload: CommandHeartbeat,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Lease-bound proof of real Muse progress; capped state, no history growth."""
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid command ID")
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if (cmd is None or cmd.status != "claimed"
            or cmd.lease_expires_at is None
            or _aware(cmd.lease_expires_at) <= _utc()
            or not cmd.lease_hash
            or not hmac.compare_digest(cmd.lease_hash,
                hashlib.sha256(payload.lease.encode()).hexdigest())):
        raise HTTPException(409, "Heartbeat rejected: command lease expired or invalid")
    now = _utc()
    details = dict(cmd.details) if isinstance(cmd.details, dict) else {}
    details["lastHeartbeatAt"] = now.isoformat()
    details["progressState"] = payload.state
    details["progressNote"] = payload.note or ""
    cmd.details = details
    cmd.lease_expires_at = now + timedelta(minutes=30)
    cmd.updated_at = now
    db.commit()
    return {"ok": True, "commandId": command_id,
            "status": cmd.status, "progressState": payload.state,
            "leaseExpiresAt": cmd.lease_expires_at.isoformat()}


@router.post("/commands/{command_id}/complete")
def complete_muse_command(command_id: str, payload: CommandAck,
                          tenant: str = Depends(_muse),
                          db: Session = Depends(get_db)):
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid command ID")
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if (cmd is None or cmd.status != "claimed"
            or cmd.lease_expires_at is None
            or _aware(cmd.lease_expires_at) <= _utc()
            or not cmd.lease_hash
            or not hmac.compare_digest(cmd.lease_hash,
                  hashlib.sha256(payload.lease.encode()).hexdigest())):
        raise HTTPException(409, "Command lease invalid or expired")
    job = None if cmd.action == "execute_task" else db.get(
        CamMuseJob, (tenant, cmd.listing_key)
    )
    if cmd.action == "execute_task":
        if payload.outcome == "success":
            result = payload.result
            d = cmd.details if isinstance(cmd.details, dict) else {}
            if result is None or not _valid_task_result(d.get("taskType", ""), result):
                raise HTTPException(422, "Task result failed validation")
            stored = result.model_dump()
            if len(json.dumps(stored, ensure_ascii=False).encode("utf-8")) > 24000:
                raise HTTPException(422, "Task result exceeds 24KB")
            cmd.details = {
                **d, "result": stored, "notes": payload.notes,
                "resultValidated": True,
            }
            cmd.status = "completed"
        else:
            cmd.details = {
                **(cmd.details if isinstance(cmd.details, dict) else {}),
                "notes": payload.notes,
            }
            cmd.status = "failed"
        cmd.finished_at = _utc()
        cmd.lease_hash = None
        cmd.lease_expires_at = None
        cmd.updated_at = _utc()
        db.commit()
        return {"ok": True, **_command_status(cmd)}
    if payload.outcome == "failed":
        cmd.status = "failed"
        cmd.finished_at = _utc()
    elif cmd.phase == "review_artwork":
        qa = _qa_record(job) if job else None
        if (not isinstance(qa, dict)
                or qa.get("verdict") != "pass"
                or qa.get("artwork_sha256") != cmd.artwork_sha256
                or job.status != "pending"):
            raise HTTPException(409, "Required Muse QA verdict not verified")
        if cmd.action == "review_artwork":
            cmd.status = "reviewed"
            cmd.finished_at = _utc()
        else:
            cmd.status = "pending"
            cmd.phase = "create_video"
    else:
        receipts = job.output_receipts if job and isinstance(job.output_receipts, dict) else {}
        videos = receipts.get("videos")
        if (not job or job.status != "submitted"
                or not isinstance(videos, list) or len(videos) != 2
                or {x.get("aspectRatio") for x in videos
                    if isinstance(x, dict)} != {"9:16", "16:9"}):
            raise HTTPException(409, "Two video receipts not yet submitted to CAM")
        cmd.status = "receipt_submitted"
        cmd.finished_at = _utc()
    cmd.details = {"outcome": payload.outcome, "notes": payload.notes[:800],
                   "completedPhase": cmd.phase,
                   "videoReceiptNotCamLinked": True}
    cmd.lease_hash = None
    cmd.lease_expires_at = None
    cmd.updated_at = _utc()
    db.commit()
    return {"ok": True, **_command_status(cmd)}
