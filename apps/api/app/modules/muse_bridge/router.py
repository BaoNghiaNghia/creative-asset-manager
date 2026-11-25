"""CAM Muse Bridge API.

The Windows Scout token can push *metadata only* through the agent endpoint.
Muse has a distinct scoped credential; no login cookies, CodeLocal execution or
raw local filesystem paths are accepted. Video submission is a review receipt,
never proof of Google Drive ingestion or CAM listing linkage.
"""
from __future__ import annotations

import asyncio
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
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import Literal
from sqlalchemy import delete, select, update, func, or_, case
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db, SessionLocal
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.processing.model import ProcessingJobModel
from app.modules.etsy_spy.service import authenticate_agent
from app.modules.etsy_spy.image_agent import _private_drive_design, _checked_design_media
from .model import CamMuseJob, CamMuseCommand
from .command_queue import claimable_commands_query
from .queue_recovery import renderer_circuit, recovery_enabled as smart_queue_enabled
from .worker_accounts import (
    POLICY_VERSION, WorkerAccountConfigError, authorize_worker,
    authorize_preregistered_worker, load_roster, legacy_claims_draining,
    worker_execution_readiness, verify_lease_owner, worker_config,
)
from .worker_failover import (
    cooldown_status, failover_status, report_provider_cooldown,
)
from .story_prompts import (
    STORY_VERSION, STORY_DURATION_SECONDS, STORY_MAX_NATIVE_TAKE_SECONDS,
    STORY_FINAL_DURATION_BOUNDS, STORY_OUTPUT_CONTRACT, STORY_SOURCE_CONTRACT, build_story_spec,
)
from .garment_policy import (
    GARMENT_POLICY_VERSION, GARMENT_SKU, GARMENT_NAME, GARMENT_CONSTRUCTION,
    GARMENT_REJECT, GARMENT_QA, safe_render_title, render_context,
)
from .story_action_policy import (
    STORY_ACTION_VERSION, PLACEMENT_PROOF_VERSION,
    PLACEMENT_TARGET_MM, PLACEMENT_TOLERANCE_MM, action_plan,
)

from . import observability as obs
from . import native_placement_review as placement_review


class MuseObservedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def observed(request: Request):
            try:
                return await handler(request)
            except HTTPException as exc:
                request.state.muse_error_detail = exc.detail
                raise
        return observed


router = APIRouter(prefix="/api/v1/muse-bridge", tags=["muse-bridge"], route_class=MuseObservedRoute)
BRIDGE_VERSION = "1.4.16"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
LISTING_ID = re.compile(r"^[0-9]{5,20}$")
ARTWORK_PATH = re.compile(
    r"^/api/v1/etsy-spy/image-agents/designs/[0-9a-f-]{36}/[0-9a-f]{24}/original$"
)
STATES = {"ready_for_muse", "qa_hold", "missing_source"}
VIDEO_HOSTS = {"drive.google.com", "docs.google.com"}
# User-approved print-only photo/video contract. The actual ink-to-collar gap
# is measured along shirt fabric; never infer centimeters from MP4 pixels.
PRINT_INK_CLEARANCE_MM = 150
PRINT_ANCHOR_POLICY = "CAM_PRINT_TOP_150MM_V2"
PRINT_BOX_POLICY = "CAM_PRINT_BOX_CONSISTENCY_V2"

def _configured():
    token = os.environ.get("CAM_MUSE_BRIDGE_TOKEN", "")
    tenant = os.environ.get("CAM_MUSE_TENANT_ID", "")
    if len(token) < 48 or len(tenant) < 2:
        raise HTTPException(503, "Muse Bridge not configured")
    return token, tenant

def _muse(request: Request, authorization: str | None = Header(default=None)):
    token, tenant = _configured()
    try:
        identity = authorize_worker(
            authorization, environ=os.environ, legacy_token=token)
    except WorkerAccountConfigError as exc:
        # An invalid or half-configured dual rollout must not fall back to
        # one shared credential or allow only one account to claim jobs.
        raise HTTPException(503, "Muse worker roster is not configured") from exc
    if identity is None:
        raise HTTPException(401, "Invalid Muse credential")
    request.state.muse_worker_id = identity.id
    request.state.muse_worker_legacy = identity.legacy
    request.state.muse_tenant_id = tenant
    return tenant


def _muse_identity(request: Request) -> str:
    return getattr(request.state, "muse_worker_id", "legacy")


def _muse_dual_enabled() -> bool:
    try:
        return load_roster(os.environ) is not None
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker roster is not configured") from exc


def _assert_current_muse_policy(version: str | None) -> None:
    if _muse_dual_enabled() and version != POLICY_VERSION:
        raise HTTPException(409, "Muse worker policy changed: refresh /workers/config")


def _block_legacy_claims_during_cutover() -> None:
    """Drain only *new* leases; existing jobs can still heartbeat/finish."""
    if _muse_dual_enabled():
        return
    try:
        draining = legacy_claims_draining(os.environ)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse cutover registration invalid") from exc
    if draining:
        raise HTTPException(409, "Muse dual-worker cutover: legacy claims draining")


def _assert_muse_executor_ready(
    request: Request, *, existing_command_job: bool = False,
    review_only: bool = False,
) -> None:
    """Only Muse-01 may take normal work; Muse-02 is a cooldown standby.

    A job claim for a command that Muse-02 already owns can finish after the
    Muse-01 cooldown expires; ownership is checked by the caller.
    """
    if not _muse_dual_enabled():
        return
    identity = _muse_identity(request)
    try:
        readiness = worker_execution_readiness(os.environ, identity)
        cooldown = cooldown_status(os.environ, identity)
        route = failover_status(os.environ)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker availability unavailable") from exc
    if not readiness["ready"]:
        raise HTTPException(409, "Muse worker renderer or persistent runner not verified")
    if cooldown["cooldownActive"] and not (
            review_only and cooldown.get("reason") == "video_daily_budget_exhausted"):
        raise HTTPException(
            429, "Muse account cooling down; no new job claim permitted"
        )
    if (identity == "muse-02" and not existing_command_job
            and route["preferredWorker"] != "muse-02"):
        raise HTTPException(409, "Muse 02 is standby until Muse 01 cooldown")


def _muse_poll_gate(request: Request) -> dict | None:
    """Never offer queue work to an account unable to execute or in cooldown."""
    if not _muse_dual_enabled():
        return None
    identity = _muse_identity(request)
    try:
        capable = worker_execution_readiness(os.environ, identity)["ready"]
        cooldown = cooldown_status(os.environ, identity)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker availability unavailable") from exc
    try:
        route = failover_status(os.environ)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse failover state unavailable") from exc
    review_only = bool(cooldown["cooldownActive"]
                       and cooldown.get("reason") == "video_daily_budget_exhausted")
    request.state.muse_review_only = review_only
    if (capable and (not cooldown["cooldownActive"] or review_only)
            and (identity != "muse-02" or route["preferredWorker"] == "muse-02")):
        return None
    reason = (
        "account_cooldown" if cooldown["cooldownActive"] else
        "worker_runtime_or_renderer_not_verified" if not capable else
        "primary_worker_active"
    )
    return {
        "ok": True, "items": [], "count": 0,
        "pollAfterSeconds": max(20, min(cooldown["secondsRemaining"] or 60, 300)),
        "blockedReason": reason,
        "cooldownUntilUtc": cooldown["cooldownUntilUtc"],
        "recommendedAction": "refresh_workers_config",
        "noClaim": True,
    }


def _assert_muse_owner(request: Request, details: dict | None) -> None:
    if not verify_lease_owner(
        details, _muse_identity(request), enabled=_muse_dual_enabled()
    ):
        raise HTTPException(403, "This Muse worker does not own the lease")


def _assert_muse_video_owner(request: Request, job: CamMuseJob) -> None:
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    _assert_muse_owner(request, {"museWorkerOwner": receipts.get("museWorkerOwner")})


def _assert_active_job_command_owner(
    request: Request, db: Session, tenant: str, listing_id: str, phase: str
) -> None:
    if not _muse_dual_enabled():
        return
    # A job can only be claimed or reviewed by the account holding CAM's
    # current, unexpired command lease for the same listing and phase.
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.listing_key == listing_id,
        CamMuseCommand.status == "claimed",
        CamMuseCommand.phase == phase,
        CamMuseCommand.lease_expires_at > _utc(),
    ).order_by(CamMuseCommand.created_at.desc()).limit(1).with_for_update())
    if cmd is None:
        raise HTTPException(409, "Claim a CAM Muse command before the job")
    _assert_muse_owner(request, cmd.details)

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

from .story_lite import (
    LITE_POLICY, LITE_OUTPUT, LITE_SOURCE, LITE_TARGET_SECONDS,
    LITE_DURATION_BOUNDS, lite_story_spec, worker_policy as lite_worker_policy,
)


class ClaimRequest(BaseModel):
    worker: str = Field(default="muse", max_length=80)
    # Explicit worker capability; the legacy Muse scheduler remains supported.
    storyVersion: Literal["CAM_STORY_REAL_LIFE_V3_3"] | None = None
    storyOutputContract: Literal["CAM_STORY_12S_TWO_TAKE_V1", "CAM_STORY_NATIVE_FLEX_V1"] | None = None
    storySourceContract: Literal["CAM_STORY_DUAL_HERO_FACE_V1", "CAM_STORY_CHARACTER_SOURCE_V1"] | None = None
    storyActionContract: Literal["CAM_C1717_12S_REAL_ACTION_V1"] | None = None
    placementProofContract: Literal["CAM_C1717_150MM_SOURCE_MEASUREMENT_V1"] | None = None


class CalibratedHeroGeometry(BaseModel):
    """Measured same-fabric-plane geometry for deterministic *plain-shirt* print.

    Values are worker reported; metadata alone cannot prove a real-world
    physical scale. Independent source/video visual QA is still mandatory.
    """
    lowerCollarBottomYpx: float = Field(ge=0, le=8192)
    garmentReferenceWidthPx: float = Field(gt=10, le=8192)
    garmentReferenceWidthMm: float = Field(ge=350, le=750)
    printCenterXpx: float = Field(ge=0, le=8192)
    printWidthMm: float = Field(ge=120, le=320)
    shirtHemYpx: float = Field(ge=0, le=8192)
    calibrationMethod: Literal[
        "measured_c1717_shirt_size_template",
        "measured_reference_on_same_fabric_plane",
    ]
    physicalReferenceType: Literal[
        "measured_marker_on_worn_garment",
        "measured_c1717_size_template_with_same_plane_verification",
    ]
    calibrationUncertaintyMm: float = Field(ge=0, le=2.5)
    plainC1717ShirtVerified: Literal[True]
    frontFabricPlaneMeasured: Literal[True]
    noExistingPrintOnPlainSourceVerified: Literal[True]


class SourcePlacementPreflight(BaseModel):
    """Worker-reported source geometry, bounded and tied to a genuine lease."""
    lease: str = Field(min_length=32, max_length=120)
    aspectRatio: Literal["9:16", "16:9"]
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceFrameSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceWidthPx: int = Field(ge=256, le=8192)
    sourceHeightPx: int = Field(ge=256, le=8192)
    lowerCollarBottomYpx: float = Field(ge=0, le=8192)
    firstInkTopYpx: float = Field(ge=0, le=8192)
    garmentReferenceWidthPx: float = Field(gt=10, le=8192)
    garmentReferenceWidthMm: float = Field(ge=250, le=1100)
    calibrationMethod: Literal[
        "measured_c1717_shirt_size_template",
        "measured_reference_on_same_fabric_plane",
        "visual_estimate_unverified",
    ]
    c1717ShortSleeveSourceVerified: bool
    nativeMasterMatchedToSource: bool
    printFullyVisible: bool
    storyActionPlanAcknowledged: bool
    preRenderSourceImage: bool = True


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
    observedPlacement: Literal["front_center", "back", "left_chest", "sleeve", "hem", "other", "unknown"] | None = None
    placementReviewId: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _native_asset_matches(db: Session, tenant: str, listing_id: str, digest: str):
    """Only completed, tenant-owned, SHA-matched Native Master records."""
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        return None, None
    jobs = db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.entity_type == "etsy_listing",
        ProcessingJobModel.entity_id == listing_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.cancellation_requested.is_(False),
        ProcessingJobModel.status == "completed",
    ).order_by(ProcessingJobModel.created_at.desc()).limit(16)).all()
    return placement_review.choose_matches(jobs, digest)


def _review_native_asset(db: Session, tenant: str, job: CamMuseJob):
    """Unknown is eligible for review only; never a render-ready master."""
    front, unknown = _native_asset_matches(db, tenant, job.listing_key, job.artwork_sha256)
    return front or (unknown if job.product_image_url else None)


def _stored_native_asset(db: Session, tenant: str, listing_id: str, digest: str):
    """Keep the original front path; unknown needs bound, accepted visual QA."""
    front, unknown = _native_asset_matches(db, tenant, listing_id, digest)
    if front:
        return front.locator
    if unknown:
        job = db.get(CamMuseJob, (tenant, listing_id))
        if (job is not None and job.artwork_sha256 == digest
                and placement_review.approved_unknown(
                    unknown, digest, job.product_image_url, _qa_record(job))):
            return unknown.locator
    return None


def _review_placement_view(db: Session, tenant: str, job: CamMuseJob):
    match = _review_native_asset(db, tenant, job)
    return placement_review.review_contract(match, job.artwork_sha256, job.product_image_url)


def _qa_record(row: CamMuseJob):
    r = row.output_receipts
    return r.get("visual_qa") if isinstance(r, dict) else None


def _story_v33_enabled() -> bool:
    """Fail-closed rollout: opt in only after Muse can deliver genuine 12s stories."""
    return os.environ.get("CAM_MUSE_STORY_V33_ENABLED", "false").strip().lower() == "true"


def _continuous_gen_enabled() -> bool:
    """Throughput-oriented lane: keep claims flowing while QA is deferred.

    Mode was explicitly requested by the operator. It never waives Native
    Master identity, garment SKU, lease, Drive integrity or video codec checks.
    """
    return os.environ.get("CAM_MUSE_CONTINUOUS_GEN_ENABLED", "true").strip().lower() == "true"


def _placement_tolerance_mm() -> int:
    # A visually estimated photo never proves a physical +/-5 mm print gap.
    # In continuous mode its 130-170 mm estimate is marked provisional.
    return 20 if _continuous_gen_enabled() else PLACEMENT_TOLERANCE_MM


def _deterministic_hero_required() -> bool:
    """Retain deterministic compositor when strict QA mode is selected."""
    return (not _continuous_gen_enabled()
            and os.environ.get("CAM_MUSE_DETERMINISTIC_HERO_REQUIRED", "true")
            .strip().lower() == "true")


def _story_v33_pilot_listings() -> frozenset[str]:
    """Explicit <=2 listing canary; malformed/oversized configuration fails closed."""
    raw = os.environ.get("CAM_MUSE_STORY_V33_PILOT_LISTING_IDS", "").strip()
    if not raw:
        return frozenset()
    ids = [part.strip() for part in raw.split(",")]
    if (not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids)
            or any(not LISTING_ID.fullmatch(item) for item in ids)):
        return frozenset()
    return frozenset(ids)


def _story_v33_bulk_enabled() -> bool:
    # Operator explicitly approved V3.3 AUTO CONTINUOUS BULK on 2026-11-23.
    # Honor a deliberate 'false' override; when no explicit environment flag
    # exists, continuous mode authorizes new V3.3 story jobs. Legacy jobs with
    # any existing media/pin remain on their original non-V3.3 protocol.
    default = "true" if _continuous_gen_enabled() else "false"
    return (_story_v33_enabled()
            and os.environ.get("CAM_MUSE_STORY_V33_BULK_ENABLED", default)
              .strip().lower() == "true")


def _story_v33_allowed_for_job(job) -> bool:
    return _story_v33_bulk_enabled() or job.listing_key in _story_v33_pilot_listings()


def _story_lite_enabled() -> bool:
    # Operator approved throughput-first Story Lite on 2026-11-24.
    # A deliberate false override retains the strict V3.3 workflow.
    default = "true" if _continuous_gen_enabled() else "false"
    return (_story_v33_enabled() and
            os.getenv("CAM_MUSE_STORY_LITE_ENABLED", default).strip().lower() == "true")


def _story_lite_pin(pin: dict | None) -> bool:
    return bool(isinstance(pin, dict) and
                pin.get("storyOutputContract") == LITE_OUTPUT and
                pin.get("storySourceContract") == LITE_SOURCE and
                pin.get("creativePolicy") == LITE_POLICY)


def _story_lite_for_job(job) -> bool:
    pin = _pinned_story_v33(job)
    if _story_lite_pin(pin):
        return True
    if not _story_lite_enabled() or not _story_v33_allowed_for_job(job):
        return False
    rec = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    if rec.get("drive_uploads") or rec.get("videos") or rec.get("story_action_v1"):
        return False
    if rec.get("hero_sources") and not pin:
        return False
    if job.status == "pending":
        return True
    deadline = _aware(getattr(job, "lease_expires_at", None))
    return job.status == "claimed" and deadline is not None and deadline <= _utc()


def _pinned_story_v33(job) -> dict | None:
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    pin = receipts.get("story_contract_v33")
    if (isinstance(pin, dict)
            and pin.get("storyVersion") == STORY_VERSION
            and pin.get("artworkSha256") == job.artwork_sha256
            and pin.get("garmentSku") == "C1717"
            and ((_story_lite_pin(pin)
                  and pin.get("targetDurationSeconds") == LITE_TARGET_SECONDS)
                 or (pin.get("targetDurationSeconds") == STORY_DURATION_SECONDS
                     and pin.get("storyOutputContract") == STORY_OUTPUT_CONTRACT
                     and pin.get("storySourceContract") == STORY_SOURCE_CONTRACT))):
        return pin
    return None


def _build_job_story_spec(job):
    from .regeneration import regeneration_revisions
    revisions = regeneration_revisions(job)
    return build_story_spec(title=job.title, listing_key=job.listing_key,
                            artwork_sha256=job.artwork_sha256, garment_code="C1717",
                            **({"generation_revisions": revisions} if revisions else {}))


def _story_candidate_v33(job) -> dict | None:
    """Do not change an already-rendered or in-flight legacy video job."""
    if not job.artwork_sha256 or job.source_state != "ready_for_muse":
        return None
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    pinned = _pinned_story_v33(job)
    if not pinned:
        # A retry without any saved video/hero may safely adopt V3.3.
        # Preserve active leases, completed/partial video jobs and V1 pins.
        if job.status == "pending":
            available = True
        elif job.status == "claimed":
            deadline = _aware(getattr(job, "lease_expires_at", None))
            available = deadline is not None and deadline <= _utc()
        else:
            available = False
        if (not available or job.attempts >= 5
                or receipts.get("drive_uploads") or receipts.get("videos")
                or receipts.get("hero_sources") or receipts.get("story_action_v1")):
            return None
    try:
        # CAM Muse video jobs currently have a fixed, validated C1717 contract.
        # Never infer C1717/G180/G185 from a marketplace listing title.
        base = _build_job_story_spec(job)
        return lite_story_spec(base) if _story_lite_for_job(job) else base
    except ValueError:
        return None


def _compact_story_spec(
    story: dict | None, *, include_prompt: bool = False,
    aspect: str | None = None,
) -> dict | None:
    """Keep claim-discovery JSON below Muse client response truncation limits.

    The full spec is accessible only on explicit recovery request. Immutable
    hero IDs and seeds are preserved by the story-contract recovery endpoint.
    """
    if not isinstance(story, dict):
        return None
    if story.get("creativePolicy") == LITE_POLICY:
        compact = {k: v for k, v in story.items() if k != "aspectPlans"}
        compact["aspectPlans"] = {
            ratio: plan for ratio, plan in story["aspectPlans"].items()
            if aspect is None or ratio == aspect
        }
        if aspect is not None:
            compact["narrativePrompt"] = compact["aspectPlans"][aspect]["prompt"]
        return compact
    plans = {}
    for ratio in ("9:16", "16:9"):
        if aspect is not None and ratio != aspect:
            continue
        plan = (story.get("aspectPlans") or {}).get(ratio)
        if not isinstance(plan, dict):
            continue
        part = {
            key: plan.get(key)
            for key in (
                "aspectRatio", "heroRequestId", "heroSeed", "storyId",
                "meaningfulAction", "cameraDirection",
            )
        }
        for shot in ("shotA", "shotB"):
            shot_data = plan.get(shot)
            if isinstance(shot_data, dict):
                part[shot] = {"action": shot_data.get("action")}
        if include_prompt:
            part["prompt"] = plan.get("prompt")
        plans[ratio] = part
    return {
        "storyVersion": story.get("storyVersion"),
        "storyOutputContract": story.get("storyOutputContract"),
        "storySourceContract": story.get("storySourceContract"),
        "storyId": story.get("storyId"),
        "storyTheme": story.get("storyTheme"),
        "targetDurationSeconds": story.get("targetDurationSeconds"),
        "aspectPlans": plans,
    }


VIDEO_ASPECTS = ("9:16", "16:9")


def _video_aspect_phase():
    value = os.getenv("CAM_MUSE_ASPECT_PHASE", "").strip().lower()
    if value in ("both", "vertical_first"):
        return value
    return "vertical_first" if _continuous_gen_enabled() else "both"


def _phase_required_aspects():
    return ["9:16"] if _video_aspect_phase() == "vertical_first" else list(VIDEO_ASPECTS)


def _pinned_work_aspects(details):
    # Existing pre-cutover leases retain the two-aspect completion contract.
    if isinstance(details, dict) and details.get("requiredVideoAspectRatios") in (["9:16"], ["16:9"]):
        return list(details["requiredVideoAspectRatios"])
    return list(VIDEO_ASPECTS)


def _job_required_aspects(job):
    if (job.status == "claimed" and _aware(job.lease_expires_at) is not None
            and _aware(job.lease_expires_at) > _utc()):
        return _pinned_work_aspects((job.output_receipts or {}).get("videoWorkScope"))
    from .regeneration import requested_aspects
    return requested_aspects(job) or _phase_required_aspects()


def _aspect_work_view(job, required=None):
    required = list(required) if required is not None else _job_required_aspects(job)
    saved = _verified_video_ratios(job)
    missing = [r for r in VIDEO_ASPECTS if r not in saved]
    return {
        "videoAspectPhase": "vertical_first" if required == ["9:16"] else "both",
        "requiredVideoAspectRatios": required,
        "savedVideoAspectRatios": sorted(saved),
        "missingVideoAspectRatios": [r for r in required if r not in saved],
        "allMissingVideoAspectRatios": missing,
        "deferredVideoAspectRatios": [r for r in missing if r not in required],
        "verticalReady": "9:16" in saved,
        "allVideoAspectsComplete": not missing,
    }


def _video_job_claim_contract(job, *, required_aspects=None) -> dict:
    """Exact job-claim contract for THIS listing, not a global rollout hint.

    New eligible V3.3 jobs must explicitly acknowledge V3.3 before pinning;
    already saved/pinned V3.1 work must not change protocol mid-listing.
    Does not grant a lease or mark generated output as QA passed.
    """
    story_spec = _story_candidate_v33(job)
    story = bool(story_spec and (
        _pinned_story_v33(job) or _story_v33_allowed_for_job(job)
    ))
    lite = bool(story and story_spec.get("creativePolicy") == LITE_POLICY)
    if lite:
        body = {
            "storyVersion": STORY_VERSION,
            "storyOutputContract": LITE_OUTPUT,
            "storySourceContract": LITE_SOURCE,
        }
        workflow = STORY_VERSION
    elif story:
        body = {
            "storyVersion": STORY_VERSION,
            "storyOutputContract": STORY_OUTPUT_CONTRACT,
            "storySourceContract": STORY_SOURCE_CONTRACT,
            "storyActionContract": STORY_ACTION_VERSION,
            "placementProofContract": PLACEMENT_PROOF_VERSION,
        }
        workflow = STORY_VERSION
    else:
        body = {}
        if _pinned_action_story_v1(job) or _fresh_action_story_v1(job):
            body = {
                "storyActionContract": STORY_ACTION_VERSION,
                "placementProofContract": PLACEMENT_PROOF_VERSION,
            }
        workflow = (
            STORY_ACTION_VERSION if body else "CAM_LEGACY_VIDEO_CONTINUATION"
        )
    scope = _aspect_work_view(job, required_aspects)
    saved = scope["savedVideoAspectRatios"]
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    resume = {}
    for ratio in scope["missingVideoAspectRatios"]:
        if ratio in saved:
            continue
        hero = (receipts.get("hero_sources") or {}).get(ratio)
        proof = (receipts.get("story_action_preflight") or {}).get(ratio)
        if not isinstance(hero, dict) or hero.get("storageVerified") is not True:
            continue
        sha = hero.get("sha256")
        if not isinstance(sha, str) or not HEX64.fullmatch(sha):
            continue
        matching_proof = (
            isinstance(proof, dict)
            and proof.get("sourceFrameSha256") == sha
            and proof.get("artworkSha256") == job.artwork_sha256
            and proof.get("policyVersion") == PLACEMENT_PROOF_VERSION
        )
        resume[ratio] = {
            "heroSha256": sha,
            "heroStorageVerified": True,
            "preflightMatchesHeroAndArtwork": matching_proof,
            "reuseHero": matching_proof,
            "optionalExistingHero": lite,
            "workerReportedPlacementMm": (
                proof.get("workerCalculatedGapMm") if matching_proof else None
            ),
            "heroRequestId": hero.get("heroRequestId"),
            "heroSeed": hero.get("heroSeed"),
            "nextStep": ("render_one_native_clip_if_source_visually_suitable" if lite else
                         "render_missing_takes" if matching_proof else "verify_hero_preflight"),
        }
    return {
        "path": f"/api/v1/muse-bridge/jobs/{job.listing_key}/claim",
        "videoStatusPath": f"/api/v1/muse-bridge/jobs/{job.listing_key}/video-status",
        "method": "POST",
        "body": body,
        "workerField": "Use /workers/config -> workerId for the authorized Muse account",
        "workflowVersion": workflow,
        "creativePolicy": LITE_POLICY if lite else None,
        "renderPolicy": lite_worker_policy() if lite else None,
        "requiredVideoUploadHeaders": (
            ["X-Muse-Lease", "X-CAM-Motion-Version", "X-CAM-Story-Output-Contract"]
            if lite else None
        ),
        "storySpecV33": _compact_story_spec(story_spec) if story else None,
        "storyContractRecoveryPath": (
            f"/api/v1/muse-bridge/jobs/{job.listing_key}/story-contract"
            if story else None
        ),
        **scope,
        "requireExactStoryAcknowledgement": story,
        "preserveSavedVideoAndLegacyContract": not story,
        "regenerateSavedRatios": False,
        "resumeByAspect": resume,
        "reuseLocallyBankedTakeOnlyAfterSourceAndStoryQa": True,
        "forceFreshVideoAspectRatios": (receipts.get("videoRegeneration") or {}).get("requiredVideoAspectRatios", []),
        "videoGenerationRevisionByAspect": (receipts.get("videoGenerationRevisionByAspect") or {}),
    }


def _pinned_action_story_v1(job) -> dict | None:
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    pin = receipts.get("story_action_v1")
    if (isinstance(pin, dict)
            and pin.get("policyVersion") == STORY_ACTION_VERSION
            and pin.get("placementPolicyVersion") == PLACEMENT_PROOF_VERSION
            and pin.get("artworkSha256") == job.artwork_sha256
            and pin.get("garmentSku") == GARMENT_SKU):
        return pin
    return None


def _fresh_action_story_v1(job) -> bool:
    """Pristine unclaimed or expired-lease video; never mutate a live render.

    Prior failed claims without any stored video are eligible for a proper
    story retry. Jobs with even one saved aspect retain their pinned legacy
    flow rather than mixing output protocols inside a listing.
    """
    if (_pinned_story_v33(job) or _story_v33_allowed_for_job(job)
            or not job.artwork_sha256 or job.source_state != "ready_for_muse"
            or job.attempts >= 5):
        return False
    if job.status == "pending":
        available = True
    elif job.status == "claimed":
        deadline = _aware(getattr(job, "lease_expires_at", None))
        available = deadline is not None and deadline <= _utc()
    else:
        available = False
    if not available:
        return False
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    return not (receipts.get("drive_uploads") or receipts.get("videos")
                or receipts.get("hero_sources") or receipts.get("story_action_v1"))


def _action_story_candidate_v1(job) -> dict | None:
    if not (_fresh_action_story_v1(job) or _pinned_action_story_v1(job)):
        return None
    try:
        return action_plan(
            title=job.title, listing_key=job.listing_key,
            artwork_sha256=job.artwork_sha256,
        )
    except ValueError:
        return None


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
            "visible, undistorted, and at the exact approved physical scale. "
            "This is FLAT INK, never embroidery or stitched patches. In EVERY "
            "frame and aspect ratio the TOPMOST PRINT INK stays exactly "
            "150mm (15cm) below the LOWER collar rib along real garment fabric, "
            "matching approved folded/worn listing photos at the SAME width, "
            "size, proportion and horizontal anchor. No print drift."
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
            "print moved from the 150mm collar-to-topmost-ink physical anchor",
            "embroidery, stitches, patches, or raised print instead of flat ink",
        ],
        "qaMethod": (
            "Watch the whole video for human motion and clothing realism, "
            "then inspect early, middle and late frames. QA is never implied "
            "merely by a stored MP4 or an asserted motion-version header."
        ),
        "preserveNativeMaster": True,
        "designTopBelowCollarCm": 15,
                "designTopBelowCollarMm": PRINT_INK_CLEARANCE_MM,
                "placementPolicy": PRINT_ANCHOR_POLICY,
                "printBoxPolicy": PRINT_BOX_POLICY,
                "decorationMethod": "flat_ink_print_only",
        "formats": ["9:16", "16:9"],
        "completedVideoPolicy": "do_not_regenerate_without_user_approval",
        "motionVersionUploadHeader": "X-CAM-Motion-Version",
        "noOpenAiApi": True,
    }


def _lite_render_context(context: dict) -> dict:
    # The legacy garment context contains physical placement instructions.
    # Do not feed those instructions to a Story Lite worker.
    result = dict(context)
    for key in ("firstInkBelowLowerCollarMm", "preUploadVisualQaChecks",
                "preUploadVisualQaExecutor", "reRenderPolicy"):
        result.pop(key, None)
    result["preUploadVisualQaRequired"] = False
    result["renderInstruction"] = (
        "Use an adult wearing the approved C1717 short-sleeve tee and exact "
        "Native Master design. Match the source character and create a simple "
        "natural event. No calibrated placement or timed visual proof required."
    )
    result["creativePolicy"] = LITE_POLICY
    return result


def _compact_discovery_item(item: dict) -> dict:
    """Bound the list response; per-listing story recovery remains authoritative."""
    fields = (
        "commandId", "listingId", "action", "status", "phase", "nextStep",
        "artworkSha256", "attempts", "leaseExpired", "leaseExpiresAt", "createdAt",
        "title", "designTheme", "creativePolicy", "effectiveVideoWorkflowVersion",
        "storyEnabled", "storyCapabilityRequired", "storyPlacementPreflightRequired",
        "storyActionContract", "placementProofContract", "actionStoryRequired",
        "savedVideoAspectRatios", "missingVideoAspectRatios", "savedAspectRatios",
        "missingAspectRatios", "renderRetry", "reviewPath", "jobsPath",
        "artworkPath", "uploadPaths", "claimCommandPath", "completeCommandPath",
        "heartbeatCommandPath", "claimVideoJobPath", "videoJobClaim", "task",
        "heroUploadRequired", "targetDurationSeconds", "savedHeroAspectRatios",
        "camLinked", "videoGenerationEnabled", "videoExecutor", "placementReview",
        "videoAspectPhase", "requiredVideoAspectRatios", "allMissingVideoAspectRatios",
        "deferredVideoAspectRatios", "verticalReady", "allVideoAspectsComplete",
    )
    out = {k: item[k] for k in fields if k in item and item[k] is not None}
    claim = out.get("videoJobClaim")
    if isinstance(claim, dict):
        claim = dict(claim)
        story = claim.get("storySpecV33")
        if isinstance(story, dict):
            summary = {k: story[k] for k in (
                "storyVersion", "storyOutputContract", "storySourceContract",
                "storyId", "storyTheme", "targetDurationSeconds", "creativePolicy",
            ) if k in story}
            summary["aspectPlans"] = {
                ratio: {k: plan[k] for k in ("heroRequestId", "heroSeed", "storyId")
                        if k in plan}
                for ratio, plan in (story.get("aspectPlans") or {}).items()
            }
            claim["storySpecV33"] = summary
            claim["storySpecMode"] = "summary_fetch_full_per_listing"
        policy = claim.get("renderPolicy")
        if isinstance(policy, dict):
            claim["renderPolicy"] = {k: policy[k] for k in (
                "version", "preferredNativeTakesPerAspect", "preferredDurationSeconds",
                "acceptedNativeDurationSeconds", "placementGeometryRequired",
                "twoTakesRequired", "heroUploadRequired",
            ) if k in policy}
        out["videoJobClaim"] = claim
    if item.get("listingId"):
        lid = item["listingId"]
        cid = item.get("commandId")
        if cid:
            for action, field in (("claim","claimCommandPath"),
                                  ("heartbeat","heartbeatCommandPath"),
                                  ("complete","completeCommandPath")):
                out.setdefault(field, f"/api/v1/muse-bridge/commands/{cid}/{action}")
        out["detailsAfterClaim"] = True
        if isinstance(claim, dict) and claim.get("storyContractRecoveryPath"):
            out["storyContractRecoveryPath"] = claim["storyContractRecoveryPath"]
    return out


def _row_view(job):
    story_preview = _story_candidate_v33(job)
    story_active = bool(
        story_preview and (_pinned_story_v33(job) or _story_v33_allowed_for_job(job))
    )
    action_story = _action_story_candidate_v1(job) if not story_active else None
    garment = render_context(title=job.title, artwork_sha256=job.artwork_sha256)
    if story_active and story_preview.get("creativePolicy") == LITE_POLICY:
        garment = _lite_render_context(garment)
    # The raw marketplace title stays in CAM DB for Etsy SEO/audit. Never
    # send it as the Muse render title or allow its apparel words to change
    # the approved garment. Original Etsy photos are for ARTWORK comparison
    # only during QA hold, never the video start frame.
    source_review = job.status == "qa_hold"
    response = {"listingId": job.listing_key,
            "title": garment["renderTitle"],
            "designTheme": garment["designTheme"],
            "renderContext": garment,
            "sourceTitleStoredInCam": True,
            "keyword": safe_render_title(job.keyword) if job.keyword else None,
            "keywordRole": "sanitized_design_discovery_hint_only",
            "status": job.status,
            "pickedAt": job.picked_at, "artworkSha256": job.artwork_sha256,
            "artworkAssetPath": job.artwork_asset_path,
            "artworkUrl": f"/api/v1/muse-bridge/jobs/{job.listing_key}/artwork" if job.artwork_asset_path else None,
            "productImageUrl": job.product_image_url if source_review else None,
            "sourceProductImageRole": "design_comparison_only" if source_review else "not_a_video_start_frame",
            "attempts": job.attempts,
            "videoSpec": {
                "formats": _job_required_aspects(job), "targetDurationSeconds": 12,
                **_aspect_work_view(job),
                "garment": "Comfort Colors C1717", "printPosition": "front_center",
                "garmentSku": GARMENT_SKU,
                "garmentLock": garment,
                "garmentPolicyVersion": GARMENT_POLICY_VERSION,
                "designTheme": garment["designTheme"],
                "renderTitle": garment["renderTitle"],
                "titleSourcePolicy": "untrusted_etsy_title_metadata_only",
                "originalTitleMustNotBePassedToRenderer": True,
                "referenceImagePolicy": {
                    "artwork": "CAM Native Master PNG only",
                    "etsyProductImage": "artwork comparison only; NEVER image-to-video start frame",
                    "videoStartFrame": "approved worn C1717 hero or newly generated C1717 image",
                    "requireC1717CorrectShirtBeforeRender": True,
                },
                "preUploadGarmentQaRequired": True,
                "preUploadGarmentQaChecks": list(GARMENT_QA),
                "garmentMismatchDisposition": "GARMENT_MISMATCH_HOLD_do_not_upload",
                "designTopBelowCollarCm": 15,
                "designTopBelowCollarMm": PRINT_INK_CLEARANCE_MM,
                "placementPolicy": PRINT_ANCHOR_POLICY,
                "printBoxPolicy": PRINT_BOX_POLICY,
                "decorationMethod": "flat_ink_print_only",
                "artworkShaStrict": True,
                "motionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_1",
                "pilotMotionSpecV32": _motion_v32_pilot_spec(),
                "motionVersionUploadHeader": "X-CAM-Motion-Version",
                "motionStyle": "unposed candid documentary footage; real-time human behavior, not fashion acting or a slow-motion portrait",
                "startFramePolicy": (
                    garment["renderInstruction"] + " "
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
                    garment["renderInstruction"] + " "
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
                    "visible, stable FLAT INK front print, never embroidery. The first ink "
                    "stays exactly 150mm (15cm) below LOWER collar rib along "
                    "the fabric in every frame; match approved still images "
                    "for print width, size, aspect and horizontal position. "
                    "Preserve original scene objects; "
                    "do not invent a coffee cup or other prop."
                ),
                "motionQaReject": [
                    "GARMENT_MISMATCH: garment is a hoodie, sweatshirt, sweater or long sleeve",
                    "GARMENT_MISMATCH: hood, drawstrings, fleece, kangaroo pocket or ribbed cuffs",
                    "GARMENT_MISMATCH: design applied to unapproved garment or start frame",
                    "face aimed into harsh direct sunlight with no natural shade or crop alternative",
                    "forced wide-eyed stare or unnaturally fixed smile in bright sun",
                    "sustained eyelid closure inconsistent with the scene and human reaction",
                    "repeated slow head tilts, head rolls, nodding loops or robotic oscillation",
                    "fixed smiling expression for most of the clip",
                    "slow-motion or exaggerated acting instead of a motivated everyday action",
                    "unmotivated hand gestures or hands obscuring the front print",
                    "unrealistic hand, face, neck, fabric or artwork deformation",
                    "sudden camera jumps, hard scene cuts or looped body gestures",
                    "flat-ink artwork shifted from the 150mm below-collar anchor",
                    "embroidered, stitched or raised graphic instead of flat print",
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
    if story_active and story_preview.get("creativePolicy") == LITE_POLICY:
        # Do not leak contradictory V3.1/V3.3 strict prompts or geometry gates.
        original = response["videoSpec"]
        contract = _video_job_claim_contract(job)
        response["videoSpec"] = {
            "formats": original["formats"],
            "garment": original["garment"],
            "garmentSku": original["garmentSku"],
            "garmentLock": original["garmentLock"],
            "artworkShaStrict": True,
            "storyEnabled": True,
            "actionStoryEnabled": False,
            "creativePolicy": LITE_POLICY,
            "automationMode": LITE_POLICY,
            "storySpecV33": story_preview,
            "videoJobClaim": contract,
            "targetDurationSeconds": LITE_TARGET_SECONDS,
            "acceptedDurationSeconds": list(LITE_DURATION_BOUNDS),
            "nativeTakesPerAspectPreferred": 1,
            "motionPromptVersion": STORY_VERSION,
            "storyUploadMotionVersionHeader": STORY_VERSION,
            "storyUploadOutputContractHeader": LITE_OUTPUT,
            "storySourceContract": LITE_SOURCE,
            "motionPrompt": story_preview["narrativePrompt"],
            "motionPromptByAspect": {
                ratio: plan["prompt"] for ratio, plan in story_preview["aspectPlans"].items()
            },
            "startFramePolicy": "Use a suitable approved worn C1717 image; match artwork and actor.",
            "heroUploadRequired": False,
            "requiresMeasuredC1717SourceBeforeRender": False,
            "requiresSourceHeroShaMatchedPreflightForEachAspect": False,
            "physicalMillimeterCertificationRequired": False,
            "deterministicPrintPlacementRequired": False,
            "printSurfaceConformityQaRequired": False,
            "storyQaRequired": False,
            "preUploadGarmentQaRequired": False,
            "faceVisibilityQaRequired": False,
            "heroSourceQaRequired": False,
            "manualVideoApprovalRequiredForLinkage": False,
            "videoUploadPaths": original["videoUploadPaths"],
            "uploadContentType": "video/mp4",
            "uploadLeaseHeader": "X-Muse-Lease",
            "requiredVideoUploadHeaders": contract["requiredVideoUploadHeaders"],
            "resumeByAspect": contract["resumeByAspect"],
            **_aspect_work_view(job),
            "renderPolicy": lite_worker_policy(),
        }
        return response
    if action_story is not None:
        video = response["videoSpec"]
        video["actionStoryEnabled"] = True
        video["storyActionContract"] = STORY_ACTION_VERSION
        video["placementProofContract"] = PLACEMENT_PROOF_VERSION
        video["placementPreflightPath"] = (
            f"/api/v1/muse-bridge/jobs/{job.listing_key}/placement/preflight"
        )
        video["requiresClaimAcknowledgement"] = True
        video["requiresMeasuredC1717SourceBeforeRender"] = True
        video["sourcePrintTopOffsetMm"] = PLACEMENT_TARGET_MM
        video["sourcePlacementToleranceMm"] = _placement_tolerance_mm()
        video["motionPromptVersion"] = STORY_ACTION_VERSION
        video["storyActionSpec"] = action_story
        video["motionPromptByAspect"] = {
            aspect: plan["prompt"]
            for aspect, plan in action_story["actionPlansByAspect"].items()
        }
        video["motionPrompt"] = video["motionPromptByAspect"]["9:16"]
        video["motionQaReject"] += action_story["disallowedMotion"] + [
            "print placement not measured from collar bottom on calibrated C1717 shirt",
            "first ink is implausibly close to the collar or outside the "
            "130-170mm visual estimate band; do not pretend this is a physical measurement",
            "shirt is static or action is only head-turn, breathing or sigh",
            "two six-second takes lack one coherent event or physical displacement",
        ]
        video["preUploadActionAndPlacementQa"] = {
            "minGenuineHumanActionSeconds": 6,
            "inspectSeconds": [0.5, 3.0, 6.0, 9.0, 11.5],
            "workerReportOnlyNotIndependentCamApproval": True,
            "ifMeasurementMissing": "PLACEMENT_UNVERIFIED_HOLD",
            "ifActionMissing": "STORY_ACTION_MISSING_HOLD",
        }
    else:
        response["videoSpec"]["actionStoryEnabled"] = False
    if story_preview is not None:
        video = response["videoSpec"]
        # Candidate prompt is visible for a Muse-side capability canary.
        # Legacy workers still receive their unchanged 12s V3.1 defaults.
        video["storySpecV33"] = story_preview
        video["storyEnabled"] = story_active
        video["storyUploadMotionVersionHeader"] = STORY_VERSION
        video["storyUploadOutputContractHeader"] = STORY_OUTPUT_CONTRACT
        video["storySourceContract"] = STORY_SOURCE_CONTRACT
        existing_receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
        hero_sources = existing_receipts.get("hero_sources") or {}
        video["savedHeroAspectRatios"] = [
            ratio for ratio in ("9:16", "16:9")
            if isinstance(hero_sources.get(ratio), dict)
            and hero_sources[ratio].get("storageVerified")
        ]
        video["heroSha256ByAspect"] = {
            ratio: hero_sources[ratio]["sha256"]
            for ratio in video["savedHeroAspectRatios"]
        }
        video["storyHeroUploadPaths"] = {
            "9:16": f"/api/v1/muse-bridge/jobs/{job.listing_key}/hero/vertical",
            "16:9": f"/api/v1/muse-bridge/jobs/{job.listing_key}/hero/horizontal",
        }
        video["calibratedHeroUploadPaths"] = {
            "9:16": f"/api/v1/muse-bridge/jobs/{job.listing_key}/hero/vertical/calibrated",
            "16:9": f"/api/v1/muse-bridge/jobs/{job.listing_key}/hero/horizontal/calibrated",
        }
        video["heroConstructionVersion"] = "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"
        video["deterministicPrintPlacementRequired"] = _deterministic_hero_required()
        video["calibratedHeroRequestGeometryFields"] = [
            "lowerCollarBottomYpx",
            "garmentReferenceWidthPx", "garmentReferenceWidthMm",
            "printCenterXpx", "printWidthMm", "shirtHemYpx",
            "calibrationMethod", "physicalReferenceType",
            "calibrationUncertaintyMm", "plainC1717ShirtVerified",
            "frontFabricPlaneMeasured",
            "noExistingPrintOnPlainSourceVerified",
        ]
        video["sourceCalibrationMaximumUncertaintyMm"] = 2.5
        video["heroPreparationWorkflow"] = (
            "Generate one native-ratio PLAIN unprinted C1717 on-body photo, "
            "measure physical shirt reference on its fabric plane, submit "
            "X-CAM-Hero-Geometry JSON and raw blank hero to /hero/{aspect}/calibrated. "
            "CAM overlays immutable approved Native Master bytes at the calculated "
            "150mm collar-to-ink anchor; use resulting hero SHA + calibration in "
            "preflight. Do not direct-image-generate printed text or alter "
            "the declared millimeter scale to bypass QA."
        )
        video["heroRevisionPolicy"] = (
            "On AUTO_CONTINUOUS, a wrong-print hero can be replaced up to 2 "
            "times per aspect via /hero/{aspect} with exact "
            "X-CAM-Previous-Hero-Sha256 from videoSpec.heroSha256ByAspect. "
            "Any previous matching preflight is archived and invalidated; "
            "a new preflight for the new SHA is mandatory. "
            "No replacement after an MP4 is stored for that aspect. "
            "Old Drive files remain in bounded CAM revision history."
        )
        video["heroRepairHeader"] = "X-CAM-Previous-Hero-Sha256"
        video["heroRepairMaxPerAspect"] = 2
        video["heroRepairDirectPrintedSourcesEnabled"] = _continuous_gen_enabled()
        if story_active:
            video["storyActionContract"] = STORY_ACTION_VERSION
            video["placementProofContract"] = PLACEMENT_PROOF_VERSION
            video["placementPreflightPath"] = (
                f"/api/v1/muse-bridge/jobs/{job.listing_key}/placement/preflight"
            )
            video["requiresMeasuredC1717SourceBeforeRender"] = True
            video["requiresSourceHeroShaMatchedPreflightForEachAspect"] = True
            video["sourcePrintTopOffsetMm"] = PLACEMENT_TARGET_MM
            video["sourcePlacementToleranceMm"] = _placement_tolerance_mm()
            video["v4FabricPilotEnabled"] = (
                _v4_fabric_pilot_enabled()
                and job.listing_key in _story_v33_pilot_listings()
            )
            video["v4FabricUploadPaths"] = {
                "9:16": f"/api/v1/muse-bridge/jobs/{job.listing_key}/videos/vertical/fabric-composite",
                "16:9": f"/api/v1/muse-bridge/jobs/{job.listing_key}/videos/horizontal/fabric-composite",
            }
            video["v4FabricContract"] = "CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1"
            video["v4FabricInputLimits"] = {
                "maxSourceBytes": 41943040,
                "maxVideoPixels": 1000000,
                "maxFrameRate": 30,
                "maxFrames": 400,
                "requiredDurationSeconds": 12,
                "requiredTakeCount": 2,
                "requiredCutSecond": 6,
                "renderer": "CAM_cpu_opencv_dense_flow_and_ffmpeg",
                "realHumanClothTrackingQa": "pending_independent_review",
            }
            video["v4FabricInput"] = (
                "Render one real original 12-second BLANK/unprinted C1717 "
                "moving-shirt MP4 per aspect, containing two independent "
                "real six-second takes. Measure a verified front shirt polygon, "
                "collar-to-ink reference and true cloth width at the start of "
                "EACH take. Send raw video/mp4 + X-CAM-V4-Fabric-Geometry "
                "(takeA,takeB,cutSecond=6.0,workerBlankVideoVerified=true,"
                "twoGenuineSourceTakesVerified=true,sourceShirtSku=C1717,"
                "sourceNativeAspectVerified=true,"
                "calibrationBasis=measured_on_same_shirt_fabric_plane) "
                "with exact V4 contract, lease and original hero SHA/request. "
                "CAM composites Native Master across frames. Do not render "
                "a design directly in AI video; do not reuse a crop for another aspect. "
                "Fail FABRIC_SURFACE_TRACKING_BLOCKED if source cannot track. "
                "Geometry is worker-attested; physical placement remains pending QA."
            )
            video["fabricConformityContract"] = "CAM_PRINT_SURFACE_TRACK_AND_LIGHT_V1"
            video["printSurfaceConformityQaRequired"] = True
            video["fabricPrintRenderingMethod"] = (
                "Use a per-frame tracked torso/fabric surface mesh or supported "
                "renderer-native surface deformation with physically plausible "
                "perspective, wrinkles, occlusion and cloth-correlated shading. "
                "The exact approved Native Master characters and ink colors "
                "must remain recognizable while deforming naturally with the shirt. "
                "A single static alpha-composited rectangle, planar homography "
                "alone, rigid sticker, floating overlay or camera-only transform "
                "does NOT satisfy this requirement. If unavailable, report "
                "FABRIC_SURFACE_TRACKING_BLOCKED; do not upload or mark QA PASS."
            )
            video["fabricSurfaceQaRejectReasons"] = [
                "sticker-like flat rectangle across a curved torso",
                "design edges float above shirt folds or shading",
                "print remains rigid as shirt wrinkles or stretches",
                "print fails to change perspective with chest rotation",
                "print crosses arms, fabric occlusion or garment boundary",
                "texture, ink opacity and lighting do not follow cotton",
            ]
            video["requiredVideoUploadHeaders"] = [
                "X-CAM-Motion-Version",
                "X-CAM-Story-Output-Contract",
                "X-CAM-Story-Source-Contract",
                "X-CAM-Story-Action-Contract",
                "X-CAM-Placement-Proof-Contract",
                "X-CAM-Source-Frame-Sha256",
                "X-CAM-Hero-Sha256",
                "X-CAM-Hero-Request-Id",
            ]
            video["motionPromptByAspect"] = {
                aspect: plan["prompt"]
                for aspect, plan in story_preview["aspectPlans"].items()
            }
            video["faceVisibleMinSeconds"] = 8
            video["independentHeroSourcesRequired"] = True
            video["startFramePolicy"] = (
                "MANDATORY V3.3: Generate TWO native-ratio, independent ORIGINAL "
                "photos of a real adult wearing a BLANK/UNPRINTED short-sleeve "
                "Comfort Colors C1717, fully visible face and jeans/khakis. "
                "No text/design on starting plain source and no Etsy mockup. "
                "Measure collar and same-fabric-plane reference, then let CAM "
                "COMPOSITE exact Native Master pixels at 150mm; do not ask "
                "the diffusion model to produce or reposition printed lettering. "
                "Never reuse/crop/stretch a frame across aspects. "
                "Never use neck-down crop; retain a natural full face. "
                "If plain shirt or physical scale cannot be verified, HOLD."
            )
            video["camera"] = "two independent ratio-native shoots; face and print visible, no cross-aspect reframe"
            video["heroSourceQaRequired"] = True
            video["faceVisibilityQaRequired"] = True
            video["targetDurationSeconds"] = STORY_DURATION_SECONDS
            video["motionPromptVersion"] = STORY_VERSION
            video["motionPrompt"] = (
                garment["renderInstruction"] + " " + story_preview["narrativePrompt"]
            )
            video["motionQaReject"] = [
                *video["motionQaReject"], *story_preview["qaPolicy"]["reject"],
            ]
            video["storyQaRequired"] = True
            if _continuous_gen_enabled():
                # Authoritative throughput-first instruction, avoiding
                # contradictory strict blank-shirt model prompting.
                video["automationMode"] = "CAM_CONTINUOUS_VIDEO_SOFT_GATE_V1"
                video["placementPolicy"] = "visual_estimate_130_170mm_pending_qa"
                video["requiresMeasuredC1717SourceBeforeRender"] = False
                video["physicalMillimeterCertificationRequired"] = False
                video["requiresSourceHeroShaMatchedPreflightForEachAspect"] = True
                video["heroPreparationWorkflow"] = (
                    "Use an independent 9:16 or 16:9 on-body image of a real "
                    "short-sleeve Comfort Colors C1717 WITH exact approved Native "
                    "Master printed flat on cotton. Aim roughly 15cm below "
                    "collar; visual estimate 130-170mm is acceptable for a "
                    "provisional preflight. Do not certify a physical measurement "
                    "when source is AI imagery. No manual approval for every "
                    "hero; separately review visibly missing/misprinted text."
                )
                video["startFramePolicy"] = (
                    "Two independent aspect-native printed C1717 heroes; preserve "
                    "Native Master lettering and colors and print on cotton; "
                    "natural face, torso and relaxed fit; never sweatshirt, "
                    "hoodie, long sleeves, extreme sticker print or cropped "
                    "identical source between aspects."
                )
                video["motionPromptByAspect"] = {
                    ratio: (
                        "CAM continuous 12-second original fashion UGC on "
                        "Comfort Colors C1717. START FROM PROVIDED APPROVED "
                        "PRINTED HERO. Ink is physically bonded to cotton and "
                        "moves with wrinkles; do not invent new letters. "
                        "Camera mostly steady, actor moves meaningfully; "
                        f"start: {plan['shotA']['action']}; "
                        f"middle: {plan['meaningfulAction']}; "
                        f"payoff: {plan['shotB']['action']}. "
                        "Use two genuine 6-second takes, not loops or slow-motion "
                        "padding. Maintain a visually natural upper-chest print "
                        "position near 15cm below collar (130-170mm approximate "
                        "estimate). Avoid rigid sticker, unreadable text or "
                        "hand occluding the entire artwork."
                    )
                    for ratio, plan in story_preview["aspectPlans"].items()
                }
                video["motionPrompt"] = video["motionPromptByAspect"]["9:16"]
                video["sourcePrintTopOffsetMm"] = PLACEMENT_TARGET_MM
                video["sourcePlacementToleranceMm"] = _placement_tolerance_mm()
                video["preUploadActionAndPlacementQa"] = {
                    "mode": "soft_visual_then_async_review",
                    "doNotWaitForManualPrintMeasurement": True,
                    "hardReject": ["wrong garment", "missing/incorrect artwork",
                                   "corrupt video", "wrong aspect ratio"],
                    "estimatedPlacementBandMm": [130, 170],
                    "physicalMeasurementVerified": False,
                }
    return response

@router.get("/health", include_in_schema=False)
def health():
    """Non-sensitive liveness probe for Muse connectors."""
    _configured()
    return {
        "ok": True, "service": "cam-muse-bridge", "version": BRIDGE_VERSION,
        "authentication": "Bearer", "queuePath": "/api/v1/muse-bridge/queue",
        "videoAspectPhase": _video_aspect_phase(),
        "videoGenerationEnabled": False,
        "storyV33SpecAvailable": True,
        "storyV33Enabled": _story_v33_enabled(),
        "storyV33BulkEnabled": _story_v33_bulk_enabled(),
        "storyV33DefaultForNewEligibleJobs": _story_v33_bulk_enabled(),
        "storyV33RolloutMode": (
            "bulk" if _story_v33_bulk_enabled() else
            "pilot_only" if _story_v33_enabled() else "disabled"
        ),
        "storyV33PilotConfiguredCount": len(_story_v33_pilot_listings()),
        "storyV33Version": STORY_VERSION,
        "storyLiteEnabled": _story_lite_enabled(),
        "storyLitePolicy": LITE_POLICY if _story_lite_enabled() else None,
        "storyV33TargetDurationSeconds": LITE_TARGET_SECONDS if _story_lite_enabled() else STORY_DURATION_SECONDS,
        "storyV33OutputContract": LITE_OUTPUT if _story_lite_enabled() else STORY_OUTPUT_CONTRACT,
        "storyV33SourceContract": LITE_SOURCE if _story_lite_enabled() else STORY_SOURCE_CONTRACT,
        "storyV33RequiresTwoOriginalHeroImages": not _story_lite_enabled(),
        "storyV33PlainHeroNativeMasterCompositeRequired": _deterministic_hero_required(),
        "storyV33HeroConstructionVersion": "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1",
        "storyV33MaxSourceCalibrationUncertaintyMm": 2.5,
        "storyV33RequiresFaceVisibleMinSeconds": None if _story_lite_enabled() else 8,
        "storyV33NativeRendererVerified": False,
        "continuousGenerationEnabled": _continuous_gen_enabled(),
        "continuousPrintGapToleranceMm": _placement_tolerance_mm(),
        "storyV33PrintSurfaceConformityQaRequired": not _story_lite_enabled(),
        "storyV33PrintSurfaceTrackingVerified": False,
        "storyV4FabricPilotEnabled": _v4_fabric_pilot_enabled(),
        "storyV4FabricCompositorContract": "CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1",
        "storyV4FabricTestModeProofOnly": True,
        "storyV4FabricActualOutputVerifiedWithRealPerson": False,
        "storyV4LiveHumanVideoPassedQa": False,
        "storyV33MaxNativeTakeSeconds": STORY_MAX_NATIVE_TAKE_SECONDS,
        "storyV33TwoTakeAssemblyVerified": False,
        "garmentRenderPolicyVersion": GARMENT_POLICY_VERSION,
        "garmentSku": GARMENT_SKU,
        "storyActionContract": STORY_ACTION_VERSION,
        "placementProofContract": PLACEMENT_PROOF_VERSION,
    }


def _cam_placement_policy_decision() -> dict:
    """Live operator policy exposed to Muse, without mutating tasks or queue."""
    if _story_lite_enabled():
        return {
            "decisionId": "CAM_STORY_CHARACTER_LITE_20261124_V1",
            "decision": "STORY_AND_CHARACTER_ONLY_FOR_NEW_LITE_WORK",
            "mode": "story_lite",
            "storyLiteEnabled": True,
            "placementPreflightRequired": False,
            "physicalMeasurementRequiredForGeneration": False,
            "calibratedHeroUploadOptional": True,
            "manualApprovalRequiredBeforeEveryVideo": False,
            "policy": lite_worker_policy(),
            "scope": "Only listings with the Story Lite claim contract; preserve legacy and strict partial jobs",
            "v33BulkEnabled": _story_v33_bulk_enabled(),
            "runControl": {
                "V33BulkEnabled": _story_v33_bulk_enabled(),
                "requiresMuseTaskToBeRunning": True,
                "noAutomaticDuplicateTasks": True,
            },
        }
    if _continuous_gen_enabled():
        return {
            "decisionId": "CAM_CONTINUOUS_VIDEO_SOFT_GATE_20261123_V1",
            "decision": "AUTO_CONTINUOUS_SOFT_PLACEMENT",
            "mode": "throughput_first",
            "targetMm": PLACEMENT_TARGET_MM,
            "toleranceMm": _placement_tolerance_mm(),
            "acceptanceRangeMm": [130, 170],
            "physicalMeasurementRequiredForGeneration": False,
            "physicalMeasurementVerified": False,
            "approximateSourceMeasurementMethod": "visual_estimate_unverified",
            "printPlacementResult": "estimated_pending_visual_qa",
            "nativeI2V": "ALLOWED_FROM_VISUALLY_PLAUSIBLE_C1717_PRINTED_HERO",
            "directHeroUploadPermitted": True,
            "calibratedHeroUploadOptional": True,
            "directPrintedHeroReplacement": (
                "up_to_two_per_aspect_using_X-CAM-Previous-Hero-Sha256"
            ),
            "replacementInvalidatesPriorPreflight": True,
            "replacementNeverOverwritesVerifiedVideo": True,
            "continuousVideoGenerationRequested": True,
            "keepV33PilotOnly": not _story_v33_bulk_enabled(),
            "v33BulkEnabled": _story_v33_bulk_enabled(),
            "v33DefaultForNewEligibleWork": _story_v33_bulk_enabled(),
            "continueNonPilotV31Backlog": True,
            "manualApprovalRequiredBeforeEveryVideo": False,
            "asynchronousVideoQa": True,
            "stopOnlyFor": [
                "wrong_garment_not_C1717",
                "Native_Master_missing_or_mismatched",
                "print_missing_or_unreadable",
                "corrupt_mp4_or_wrong_ratio",
                "unauthorized_claim_or_lease",
                "provider_quota_cooldown",
            ],
            "handling": {
                "softGeometry": "A 130-170mm visual estimate is provisional, not a certified physical measurement",
                "oneJobFails": "HOLD that listing and immediately continue other eligible jobs",
                "cooldown429": "Respect provider reset; automatically resume scheduled polling after cooldown",
                "queue": "Keep poll/claim/run on the existing two-minute scheduler; no duplicate worker",
                "completedVideos": "Never regenerate a verified saved MP4",
                "retries": "At most 5 claim attempts per listing, then HOLD",
            },
            "heartbeat": {
                "requiredFields": ["lease", "state"],
                "leaseSource": "command_claim_response_not_video_job_lease",
            },
            "runControl": {
                "CAMAllowsEligibleNonPilotV31": True,
                "V33BulkEnabled": _story_v33_bulk_enabled(),
                "requiresMuseTaskToBeRunning": True,
                "operatorMustResumeExternalPausedMuseTask": not _story_v33_bulk_enabled(),
                "noAutomaticDuplicateTasks": True,
            },
        }
    return {
        "decisionId": "CAM_V33_PLACEMENT_DECISION_20261123_V1",
        "decision": "KEEP_150_PLUS_MINUS_5_MM",
        "requestedRelaxationToPlusMinus20Mm": "REJECTED",
        "targetMm": PLACEMENT_TARGET_MM,
        "toleranceMm": PLACEMENT_TOLERANCE_MM,
        "acceptanceRangeMm": [
            PLACEMENT_TARGET_MM - PLACEMENT_TOLERANCE_MM,
            PLACEMENT_TARGET_MM + PLACEMENT_TOLERANCE_MM,
        ],
        "resolution": "CALIBRATED_PLAIN_C1717_HERO_PLUS_NATIVE_MASTER",
        "generateFinishedPrintWithDiffusion": False,
        "manualPixelOffsetRetries": False,
        "calibratedHeroEndpointTemplate":
            "/api/v1/muse-bridge/jobs/{listing_id}/hero/{aspect}/calibrated",
        "sourceCalibrationRequired": True,
        "maxCalibrationUncertaintyMm": 2.5,
        "sourceGeometrySchemaLocation":
            "/api/v1/muse-bridge/workers/config -> v33CalibratedHeroGeometrySchema",
        "geometry422FieldErrorLocation": "detail.issues[].field",
        "ifNoPhysicalReference": "PLACEMENT_UNVERIFIED_HOLD",
        "ifGeometryRejected": (
            "Read named 422 issue; fix JSON using the SAME verified blank "
            "hero rather than rerolling the AI print position"
        ),
        "nativeI2V": "ALLOWED_ONLY_AFTER_VERIFIED_HERO_AND_PREFLIGHT",
        "heartbeat": {
            "endpointTemplate":
                "/api/v1/muse-bridge/commands/{command_id}/heartbeat",
            "requiredFields": ["lease", "state"],
            "leaseSource": "command_claim_response_not_video_job_lease",
            "validStates": [
                "started", "researching", "collecting", "validating",
                "uploading", "blocked",
            ],
            "neverSendEmptyObject": True,
        },
        "runControl": {
            "v33BulkEnabled": False,
            "automaticallyUnpauseMuseTask": False,
            "automaticallyClaimJobs": False,
            "newPilotClaimRequiresIndependentOperatorCommand": True,
            "preserveExistingHeroAndVideoReceipts": True,
        },
    }


@router.get("/policy-decisions/print-placement")
def cam_print_placement_decision(
    response: Response, tenant: str = Depends(_muse),
):
    """Authenticated read-only CAM -> Muse policy reply to a blocked pilot.

    Do not require a new claimed command to read this response. It never
    mutates queue state, worker task, source receipts, or production policy.
    """
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok": True, **_cam_placement_policy_decision()}


@router.get("/workers/config")
def muse_worker_shared_config(
    request: Request, response: Response,
    authorization: str | None = Header(default=None),
):
    """Account-scoped policy: pending Muse-02 may read config but NEVER claim.

    This permits secure onboarding before a credential-rotating dual cutover,
    while all job/command endpoints continue to use the legacy dependency.
    """
    response.headers["Cache-Control"] = "private, no-store"
    token, _tenant = _configured()
    try:
        identity = authorize_worker(
            authorization, environ=os.environ, legacy_token=token)
        preregistered = False
        if identity is None and not _muse_dual_enabled():
            # Stable root owned registry, not part of a public API or a release.
            registry = os.environ.get(
                "CAM_MUSE_WORKER_REGISTRATION_FILE",
                "/opt/creative-asset-manager/ops/muse-worker-registration-v1.json",
            )
            identity = authorize_preregistered_worker(
                authorization, registry_file=registry)
            preregistered = identity is not None
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker registration unavailable") from exc
    if identity is None:
        raise HTTPException(401, "Invalid Muse credential")
    request.state.muse_worker_id = identity.id
    request.state.muse_tenant_id = _tenant
    active_dual = _muse_dual_enabled()
    config = worker_config(
        identity, v33_enabled=_story_v33_enabled(),
        v33_pilots=len(_story_v33_pilot_listings()))
    config["dualEnabled"] = active_dual
    config["v33BulkEnabled"] = _story_v33_bulk_enabled()
    config["v33BulkAutoContinuousOptIn"] = bool(
        _story_v33_bulk_enabled() and _continuous_gen_enabled()
    )
    config["v33BulkSource"] = (
        "explicit_environment" if "CAM_MUSE_STORY_V33_BULK_ENABLED" in os.environ
        else "operator_approved_continuous_default"
        if _story_v33_bulk_enabled() else "disabled"
    )
    config["v33RolloutMode"] = (
        "bulk" if _story_v33_bulk_enabled() else
        "pilot_only" if _story_v33_enabled() else "disabled"
    )
    config["garmentRenderPolicyVersion"] = GARMENT_POLICY_VERSION
    config["garmentSku"] = GARMENT_SKU
    config["renderSourcePolicy"] = "CAM_Native_Master_and_approved_C1717_hero_only"
    config["marketplaceTitlePolicy"] = "seo_metadata_only_never_render_instructions"
    config["preUploadGarmentVisualQaRequired"] = True
    config["garmentMismatchAction"] = "GARMENT_MISMATCH_HOLD_do_not_upload"
    config["storyActionContract"] = STORY_ACTION_VERSION
    config["placementProofContract"] = PLACEMENT_PROOF_VERSION
    config["newVideoClaimRequiresStoryAndPlacementAck"] = True
    config["newVideoUploadRequiresSourcePlacementPreflight"] = True
    config["v33DeterministicPrintPlacementRequired"] = _deterministic_hero_required()
    config["v33HeroConstruction"] = "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"
    config["v33PlainHeroGeometryHeader"] = "X-CAM-Hero-Geometry"
    config["v33CalibratedHeroGeometrySchema"] = CalibratedHeroGeometry.model_json_schema()
    config["v33PlacementPolicyDecisionPath"] = (
        "/api/v1/muse-bridge/policy-decisions/print-placement"
    )
    config["v33PlacementPolicyDecision"] = _cam_placement_policy_decision()
    config["v33GeometryValidationErrorContract"] = {
        "httpStatus": 422,
        "code": "CALIBRATED_HERO_GEOMETRY_SCHEMA_INVALID",
        "fieldErrorPath": "detail.issues[].field",
        "nonSchemaPhysicalFitMayAlsoReturn422": True,
        "neverGuessOrFalsifyPhysicalReference": True,
    }
    config["v33PreviousHeroShaHeader"] = "X-CAM-Previous-Hero-Sha256"
    config["v33DirectHeroRevisionEnabled"] = _continuous_gen_enabled()
    config["v33HeroRevisionMaxPerAspect"] = 2
    config["v33DirectHeroRevisionPolicy"] = (
        "For a failed C1717 printed hero with zero stored MP4s for the ratio, "
        "retry POST /jobs/{listing_id}/hero/{aspect} with exact "
        "X-CAM-Previous-Hero-Sha256 from the currently saved hero. "
        "Keep the same pinned heroRequestId and heroSeed; "
        "old Drive source and preflight are archived and any old preflight "
        "is invalidated; resubmit preflight using the new image SHA. "
        "Maximum 2 repairs per aspect. Never delete a verified MP4."
    )
    config["expiredClaimedCommandMayBeReclaimed"] = True
    config["expiredClaimRule"] = (
        "A claimed command whose lease has expired and attempts < 5 "
        "may be claimed again using its existing commandId. "
        "Claim a new video-job lease separately. Never reuse expired secrets."
    )
    config["commandHeartbeatBodySchema"] = CommandHeartbeat.model_json_schema()
    config["commandHeartbeatInstructions"] = (
        "POST /commands/{command_id}/heartbeat with JSON lease=the exact "
        "returned command claim lease (NOT job lease), state in "
        "started|researching|collecting|validating|uploading|blocked, "
        "and optional note <=180 chars; never send an empty JSON body"
    )
    config["v33SourceCalibrationMaxUncertaintyMm"] = 2.5
    config["continuousGenerationEnabled"] = _continuous_gen_enabled()
    config["perListingVideoJobClaimContractPath"] = (
        "/api/v1/muse-bridge/commands -> items[].videoJobClaim"
    )
    config["storyContractRecoveryPathTemplate"] = (
        "/api/v1/muse-bridge/jobs/{listing_id}/story-contract"
    )
    config["storyRecoveryPolicy"] = (
        "If a video-job claim response is truncated or loses storySpecV33, "
        "GET storyContractRecoveryPath using your Muse Bearer token. "
        "This works even while the video job lease is active, returns the "
        "exact CAM-pinned heroRequestId/heroSeed/storyId by aspect, "
        "and never claims, retries, mutates, or re-renders a job."
    )
    config["perListingJobClaimPolicy"] = (
        "Do not guess the story version from the global bulk flag or "
        "an operator note. Read the videoJobClaim.body returned for THIS "
        "command and POST it unchanged to videoJobClaim.path. "
        "The body contains the correct worker ID in /commands results. "
        "A fresh V3.3 listing needs storyVersion, storyOutputContract, "
        "storySourceContract, storyActionContract, placementProofContract; "
        "a partly saved/pinned V3.1 listing must finish ONLY its missing "
        "ratio with the legacy body. Never regenerate stored video ratios."
    )
    config["commandHeartbeatRenewsVideoJobLease"] = True
    config["continuousGenerationMode"] = (
        "VISUAL_ESTIMATE_SOFT_GATE" if _continuous_gen_enabled()
        else "MEASURED_PREFLIGHT_STRICT"
    )
    config["continuousTargetPrintGapMm"] = PLACEMENT_TARGET_MM
    config["continuousAcceptablePrintGapMm"] = [
        PLACEMENT_TARGET_MM - _placement_tolerance_mm(),
        PLACEMENT_TARGET_MM + _placement_tolerance_mm(),
    ]
    config["continuousAutoAdvanceOnSoftQaPending"] = True
    config["continuousUseNonPilotLegacyJobs"] = _continuous_gen_enabled()
    config["continuousRetryAfterCooldown"] = True
    config["rendererTransientRetryPolicy"] = {
        "version": "CAM_RENDERER_TRANSIENT_RETRY_V1",
        "workerReportedHttpStatuses": [502, 503, 504],
        "retryDelaysSeconds": [180, 360, 720],
        "maxAutomaticRetriesPerListing": 3,
        "maxClaimsPerJob": 5,
        "maxClaimsPerCommand": 5,
        "sameCommandIdAfterBackoff": True,
        "retryImmediatelyOnUpstreamError": False,
        "reuseVerifiedHeroesAndPreflight": True,
        "neverRegenerateVerifiedMP4": True,
        "instructions": (
            "If media.generate_video reports HTTP 502/503/504, stop repeated "
            "calls for that listing. Complete command outcome=failed with "
            "lease and notes containing renderer HTTP status. CAM schedules "
            "a delayed retry automatically if attempts remain. If returned "
            "status=pending and retryScheduled=true, do not blacklist or "
            "request a new command. Continue eligible listings/artwork review. "
            "On repeated provider-wide errors, stop video calls for 5 minutes "
            "and keep polling/reviewing. On 429 respect the real provider reset. "
            "Reuse SHA-bound verified hero/preflight, and any locally saved "
            "QA-passed takes after validating the same source and story."
        ),
    }
    config["queueDiscoveryPolicy"] = {
        "compactDefault": smart_queue_enabled(),
        "recommendedPath": "/api/v1/muse-bridge/commands?limit=10",
        "fullCompatibilityPath": "/api/v1/muse-bridge/commands?view=full&limit=1",
        "storySpecInList": "summary; read per-listing story-contract or claim response",
        "claimBodyIsComplete": True,
    }
    config["smartQueueRecovery"] = {
        "enabled": smart_queue_enabled(),
        "policy": "CAM_SMART_QUEUE_RECOVERY_V1",
        "sweepIntervalSeconds": 120,
        "eligibleQueueCap": 12,
        "preserveLiveLeasesAndMedia": True,
        "reviewRefillEnabled": smart_queue_enabled(),
        "reviewReserve": 4,
        "videoTarget": 8,
        "maxTransitionalQueueOverflow": 4,
        "reviewFairness": "one_review_after_two_video_completions_when_available",
        "maxJobClaims": 5,
        "rendererCircuitBreaker": {
            "enabled": smart_queue_enabled(),
            "distinctFailedListings": 2,
            "windowSeconds": 600,
            "pauseNewVideoSeconds": 300,
            "allowArtworkReview": True,
            "doNotInterruptActiveVideo": True,
            "halfOpenSingleVideoCandidate": True,
            "probeFailureReopensCircuit": True,
        },
    }
    config["automationHealthPath"] = "/api/v1/muse-bridge/automation/health"
    config["continuousOnNoClaimsInstructions"] = (
        "A 2-minute GET poll is NOT a video render. For each run, call "
        "/commands?limit=10 and claim a legitimate command. If no claim "
        "happens for 60 minutes while commands are pending, the Muse runner "
        "is stalled even if CAM dispatcher is active. Inspect Muse scheduler "
        "enablement and provider quota; do not create a duplicate worker. "
        "If a known-bad listing is already on terminal HOLD, continue the "
        "next command. Report real quota cooldown, then retry after reset."
    )
    config["continuousNeverRegenerateVerifiedVideo"] = True
    config["continuousPerJobMaxClaimAttempts"] = 5
    config["continuousOperationalInstructions"] = (
        "Auto ON. Idle scheduled polling every ~2 minutes; never wait for a scheduled tick between successful jobs. On 429 honor provider reset/cooldown; "
        "auto-resume after cooldown without operator approval. "
        + ("V3.3 is now default for all NEW eligible story jobs. "
           "Finish any already claimed or partially saved V3.1 job using "
           "its existing contract; do not restart/recreate saved videos. "
           if _story_v33_bulk_enabled() else
           "Keep V3.3 pilot-only and continue eligible nonpilot V3.1. ")
        + "Do not block entire queue for one HOLD listing. "
        "For V3.3, use independent native-aspect C1717 short-sleeve "
        "heroes with approved Native Master, estimated print 130-170mm "
        "as visual QA pending, never a certified physical measurement. "
        "Continue after all aspect ratios required by this command are verified, or terminal HOLD. "
        "Never regenerate stored videos, never claim the same listing "
        "concurrently, keep C1717 and intact original artwork."
    )
    config["v33SourceCannotClaimPhysicalScaleWithoutEvidence"] = True
    config["v33PrintSurfaceConformityQaRequired"] = True
    config["v33PrintSurfaceQaContract"] = "CAM_V33_INDEPENDENT_VIDEO_QA_V2_FABRIC_SURFACE"
    config["v33PerFrameFabricTrackingVerified"] = False
    config["v4FabricPilotEnabled"] = _v4_fabric_pilot_enabled()
    config["v4FabricCompositorAvailable"] = True
    config["v4FabricCompositorVerifiedWithRealPersonVideo"] = False
    config["v4FabricApproach"] = "per_frame_2d_dense_optical_flow_not_3d_uv_reconstruction"
    config["v4FabricRendererState"] = "PILOT_LOCAL_SYNTHETIC_PASS_REAL_SOURCE_REQUIRED"
    config["v4FabricUploadPathTemplate"] = (
        "/api/v1/muse-bridge/jobs/{listing_id}/videos/{aspect}/fabric-composite"
    )
    config["v4FabricGeometryHeader"] = "X-CAM-V4-Fabric-Geometry"
    config["v4FabricContract"] = "CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1"
    config["storyVsContentQa"] = "operator_review_required_before_v33_cam_linkage"
    config["v33SavedVideosRequireIndependentQa"] = not _story_lite_enabled()
    config["storyLiteEnabled"] = _story_lite_enabled()
    config["storyLitePolicy"] = lite_worker_policy() if _story_lite_enabled() else None
    if _story_lite_enabled():
        config.update({
            "newVideoClaimRequiresStoryAndPlacementAck": False,
            "newVideoUploadRequiresSourcePlacementPreflight": False,
            "v33DeterministicPrintPlacementRequired": False,
            "preUploadGarmentVisualQaRequired": False,
            "v33PrintSurfaceConformityQaRequired": False,
            "v4FabricPilotEnabled": False,
            "continuousGenerationMode": LITE_POLICY,
            "storyVsContentQa": "lite_links_verified_storage_content_qa_pending",
            "perListingJobClaimPolicy": (
                "Always use the exact per-listing videoJobClaim.body. "
                "New Story Lite uses three story fields plus worker; "
                "no geometry or preflight. Existing strict and V3.1 partial jobs "
                "retain their pinned upload requirements. Never regenerate saved ratios."
            ),
            "continuousOperationalInstructions": lite_worker_policy()["instructions"],
            "strictLegacyPoliciesApplyOnlyToPinnedLegacyJobs": True,
        })
        # Retain legacy schemas as explicitly scoped reference, never as
        # top-level instructions for fresh Story Lite claims.
        legacy_fields = (
            "storyActionContract", "placementProofContract",
            "v33HeroConstruction", "v33PlainHeroGeometryHeader",
            "v33CalibratedHeroGeometrySchema", "v33GeometryValidationErrorContract",
            "v33DirectHeroRevisionPolicy", "v33PreviousHeroShaHeader",
            "continuousTargetPrintGapMm", "continuousAcceptablePrintGapMm",
            "v33PrintSurfaceQaContract",
        )
        config["legacyPinnedWorkflowPolicy"] = {
            key: config.pop(key) for key in legacy_fields if key in config
        }
        config["legacyPinnedWorkflowPolicy"]["scope"] = (
            "Only use for listings whose exact live videoJobClaim.body requires legacy proof"
        )
    config["bridgeVersion"] = BRIDGE_VERSION
    config["nativePlacementReviewPolicy"] = {
        "version": placement_review.POLICY,
        "unknownPlacementReviewOnly": True,
        "passRequiresObservedFrontCenter": True,
        "instructions": "For review_artwork read GET /reviews?listing_id=... and its placementReview contract. Compare the actual source image and exact SHA-matched PNG. Echo placementReviewId and observedPlacement for unknown placement. Only pass a genuinely visible matching centered front design; retain existing QA checks. Never generate from unapproved, uncertain, or provider-refused material.",
    }
    config["runnerDispatchInstructions"] = (
        "canGenerateVideo=false and serverCanRenderVideo=false describe CAM, not Muse permission. "
        "Use executionStatus, claimAllowed and videoClaimAllowed for this worker, "
        "the actual provider availability, and the current per-command contract. "
        "Review artwork when offered; report real cooldown or other blocking reason. "
        "Do not retry a provider refusal or bypass attempt limits. "
        "A historical workflow failure is not proof that a currently eligible command is impossible."
    )
    config["observability"] = obs.contract()
    config["videoAspectPolicy"] = {
        "phase": _video_aspect_phase(),
        "requiredVideoAspectRatios": _phase_required_aspects(),
        "completeAfterRequiredVerifiedAspects": True,
        "doNotWaitForDeferredAspects": True,
        "preserveExistingLeasesAndVideos": True,
        "instructions": "Render only missingVideoAspectRatios in the exact claimed contract. "
                        "Complete the command immediately after those ratios are verified in Drive. "
                        "Deferred ratios stay pending for a later phase, not a failed job.",
    }
    config["videoThroughputPolicy"] = {
        "version": "CAM_MUSE_HERO_FIRST_PROBE_V1",
        "preferVerifiedReusableHero": True,
        "heroPriorityDoesNotWaiveSourceQa": True,
        "pollLimit": 10, "maxVerticalVideosPerInvocation": 6,
        "nextJobDelaySeconds": [2, 5], "idlePollSeconds": 120,
        "maxActiveRendererOperations": 1,
        "singleVideoProbeAfterCircuitPause": True,
        "refreshDiscoveryAfterEveryOutcome": True,
        "instructions": (
            "Use the first eligible command in server order. Reuse only an approved "
            "SHA-bound hero for the required aspect; Lite sources still require visual suitability. "
            "Persist full claim responses and both lease values securely before rendering. "
            "After a verified completion, poll immediately after the 2-5s transition; "
            "do not sleep until the next scheduled tick or add a redundant review/analysis. "
            "During half_open, render only the one offered video candidate and refresh the queue "
            "after its outcome. A verified upload closes the circuit; a fresh transient failure reopens it. "
            "If a prior lease is orphaned after its renderer definitively stopped, skip that listing "
            "without altering the lease. Never bypass provider cooldown, attempt limits, garment holds "
            "or the one active renderer rule."
        ),
    }
    config["videoUploadRecovery"] = {
        "statusPathTemplate": "/api/v1/muse-bridge/jobs/{listing_id}/video-status",
        "instructions": "After timeout, lost reply or upload 409, read status. "
                        "If the ratio is verified and its SHA matches, do not regenerate. "
                        "If missing, retry saved local MP4 under a valid lease. "
                        "Never overwrite a verified ratio.",
    }
    config["rendererTransientRetryPolicy"]["structuredCompletion"] = {
        "rendererHttpStatus": [502, 503, 504],
        "rendererErrorStage": "generate_video",
        "outcome": "failed",
        "notesStillRequired": True,
    }
    config["workerCount"] = 2 if active_dual else 1
    config["plannedWorkerCount"] = 2
    config["registrationStatus"] = (
        "REGISTERED_PENDING_ACTIVATION" if preregistered else "ACTIVE")
    try:
        readiness = worker_execution_readiness(os.environ, identity.id)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker runtime registry invalid") from exc
    config["claimAllowed"] = not preregistered and bool(readiness["ready"])
    config["readOnlyPreflight"] = preregistered
    config["executionStatus"] = (
        "READY" if config["claimAllowed"] else
        "BLOCKED_MISSING_RUNTIME_OR_RENDERER"
    )
    config["persistentRunnerVerified"] = readiness["persistentRunnerVerified"]
    config["externalRendererVerified"] = readiness["rendererVerified"]
    config["scheduledRendererSelfTestVerified"] = bool(
        readiness.get("scheduledRendererSelfTestVerified", False)
    )
    config["camOutputContractVerified"] = bool(
        readiness.get("camOutputContractVerified", False)
    )
    config["camUploadReceiptVerified"] = bool(
        readiness.get("camUploadReceiptVerified", False)
    )
    config["rendererVerificationScope"] = readiness.get("rendererVerificationScope")
    config["verificationStage"] = (
        "PRODUCTION_READY" if readiness["ready"] and readiness["rendererVerified"] else
        "CLAIM_READY_RENDERER_CANARY_PENDING" if readiness["ready"] else
        "SCHEDULED_RENDERER_PASSED_CANARY_PENDING"
        if readiness.get("scheduledRendererSelfTestVerified")
        and readiness.get("persistentRunnerVerified") else
        "RUNTIME_OR_RENDERER_NOT_VERIFIED"
    )
    config["serverCanRenderVideo"] = False
    config["rendererLocation"] = "external_account_runtime"
    if active_dual:
        try:
            own = cooldown_status(os.environ, identity.id)
            route = failover_status(os.environ)
        except WorkerAccountConfigError as exc:
            raise HTTPException(503, "Muse account cooldown state unavailable") from exc
        config["cooldown"] = own
        config["failover"] = route
        config["videoClaimAllowed"] = config["claimAllowed"] and not own["cooldownActive"]
        config["reviewClaimAllowed"] = config["claimAllowed"] and (
            not own["cooldownActive"] or own.get("reason") == "video_daily_budget_exhausted")
        if own["cooldownActive"]:
            config["claimAllowed"] = bool(config["reviewClaimAllowed"])
            config["executionStatus"] = (
                "VIDEO_COOLDOWN_REVIEW_ONLY" if config["reviewClaimAllowed"]
                else "ACCOUNT_COOLDOWN")
        if (identity.id == "muse-02"
                and route["preferredWorker"] != "muse-02"):
            config["claimAllowed"] = False
            config["videoClaimAllowed"] = False
            config["reviewClaimAllowed"] = False
            if readiness["ready"]:
                config["executionStatus"] = "STANDBY_PRIMARY_ACTIVE"
    return config


@router.get("/openapi.json", include_in_schema=False)
def scoped_openapi():
    """Publish only the Muse-facing contract, never internal CAM agent routes."""
    schema = get_openapi(
        title="CAM Muse Bridge", version=BRIDGE_VERSION,
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
    ).order_by(CamMuseJob.picked_at.asc())
    rows = db.scalars(query).all()
    rows = [row for row in rows if set(_job_required_aspects(row)) - _verified_video_ratios(row)]
    return {"items": [_row_view(x) for x in rows[:limit]]}

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


def _pilot_dispatch_readiness(tenant: str, db: Session, by_listing: dict) -> list[dict]:
    """Reveal unqueued canaries to the operator without mutating a GET.

    The dispatch timer may have no active command for a retry after a
    completed/failed command. Discovery previously hid that pending job
    because /video-work iterated only existing command rows.
    """
    output = []
    for listing in sorted(_story_v33_pilot_listings()):
        job = by_listing.get(listing) or db.get(CamMuseJob, (tenant, listing))
        if job is None:
            continue
        active = db.scalar(select(CamMuseCommand.command_id).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.listing_key == listing,
            CamMuseCommand.status.in_(("pending", "claimed")),
            CamMuseCommand.attempts < 5,
        ).limit(1))
        receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
        qa = _qa_record(job)
        lease = _aware(job.lease_expires_at)
        claimable = (job.status == "pending"
                     or (job.status == "claimed" and lease is not None and lease <= _utc()))
        ready = bool(
            _story_v33_enabled() and claimable and job.attempts < 5
            and job.source_state == "ready_for_muse"
            and job.artwork_sha256 and job.artwork_asset_path
            and isinstance(qa, dict) and qa.get("verdict") == "pass"
            and qa.get("artwork_sha256") == job.artwork_sha256
            and not receipts.get("videoCapabilityHold")
            and len(_verified_video_ratios(job)) < 2
            and _story_candidate_v33(job)
        )
        output.append({
            "listingId": listing,
            "status": job.status,
            "savedHeroAspectRatios": [
                r for r in ("9:16", "16:9")
                if isinstance((receipts.get("hero_sources") or {}).get(r), dict)
                and (receipts["hero_sources"][r]).get("storageVerified") is True
            ],
            "savedVideoAspectRatios": sorted(_verified_video_ratios(job)),
            "hasActiveOrPendingCommand": bool(active),
            "needsOperatorCommand": bool(ready and not active),
            "commandCreationRequiresOperatorCredential": True,
            "noJobClaimOrQueueMutation": True,
        })
    return output


@router.get("/video-work")
def missing_video_work(
    limit: int = Query(default=10, ge=1, le=20),
    tenant: str = Depends(_muse),
    db: Session = Depends(get_db),
    view: Literal["compact", "full"] = "compact",
    request: Request = None,
    response: Response = None,
):
    """One read-only Muse GET: discover queued listings missing one/both videos.

    Never generates a video, approves artwork, leases a job, or requeues rows.
    Muse must authenticate, claim an eligible command and complete real work.
    """
    review_only = False
    if request is not None:
        if response is not None:
            response.headers["Cache-Control"] = "private, no-store"
        denied = _muse_poll_gate(request)
        if denied is not None:
            return denied
        review_only = getattr(request.state, "muse_review_only", False)
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
    # Filter active leases in SQL BEFORE LIMIT: they must not starve later work.
    candidates = db.scalars(claimable_commands_query(
        tenant, _utc(), actions=("create_video", "review_artwork"),
    ).limit(2000)).all()

    items = []
    seen = set()
    circuit = renderer_circuit(db, tenant)
    for cmd in candidates:
        if cmd.listing_key in seen:
            continue
        if (cmd.status == "claimed"
                and (cmd.lease_expires_at is None
                     or _aware(cmd.lease_expires_at) > _utc())):
            continue
        # The 1,000-row inventory view is only for approximate counts;
        # listings beyond it must still be eligible for Muse dispatch.
        job = by_listing.get(cmd.listing_key)
        if job is None and cmd.listing_key:
            job = db.get(CamMuseJob, (tenant, cmd.listing_key))
            if job is not None:
                by_listing[cmd.listing_key] = job
        if not job or job.artwork_sha256 != cmd.artwork_sha256:
            continue
        saved = _verified_video_ratios(job)
        if len(saved) == 2:
            continue
        phase = _eligible_phase(db, tenant, job, cmd)
        if (phase is None or (phase == "create_video" and circuit["open"])
                or (review_only and phase != "review_artwork")):
            continue
        seen.add(job.listing_key)
        scope = _aspect_work_view(job)
        missing = scope["missingVideoAspectRatios"]
        story_candidate = _story_candidate_v33(job) if phase == "create_video" else None
        story_enabled = bool(
            story_candidate and (_pinned_story_v33(job) or _story_v33_allowed_for_job(job))
        )
        lid = job.listing_key
        context = render_context(title=job.title, artwork_sha256=job.artwork_sha256)
        if story_enabled and story_candidate.get("creativePolicy") == LITE_POLICY:
            context = _lite_render_context(context)
        items.append({
            "commandId": cmd.command_id,
            "listingId": lid,
            "title": context["renderTitle"],
            "designTheme": context["designTheme"],
            "renderContext": context,
            "garmentLock": context,
            "garmentPolicyVersion": GARMENT_POLICY_VERSION,
            "storyActionContract": STORY_ACTION_VERSION,
            "placementProofContract": PLACEMENT_PROOF_VERSION,
            "actionStoryRequired": bool(_action_story_candidate_v1(job)),
            "actionStoryPlan": _action_story_candidate_v1(job),
            "sourceProductImageRole": "design_comparison_only_never_video_start_frame",
            "artworkSha256": job.artwork_sha256,
            "printPlacement": {
                "policy": PRINT_ANCHOR_POLICY,
                "printBoxPolicy": PRINT_BOX_POLICY,
                "decorationMethod": "flat_ink_print_only",
                "topmostInkBelowLowerCollarMm": PRINT_INK_CLEARANCE_MM,
                "requiredAcross": ["image_01_13", "video_9_16", "video_16_9"],
                "measurement": "along garment fabric, not frame pixels",
            },
            "nextStep": phase,
            "placementReview": _review_placement_view(db, tenant, job) if phase == "review_artwork" else None,
            "missingAspectRatios": missing,
            "savedAspectRatios": [x for x in ("9:16", "16:9") if x in saved],
            "storySpecV33": story_candidate,
            "storyEnabled": story_enabled,
            "videoJobClaim": (
                _video_job_claim_contract(job)
                if phase == "create_video" else None
            ),
            "effectiveVideoWorkflowVersion": (
                STORY_VERSION if story_enabled else STORY_ACTION_VERSION
            ),
            "legacyCommandNoteOverridesStoryContract": False,
            "placementPolicyDecisionPath": (
                "/api/v1/muse-bridge/policy-decisions/print-placement"
                if story_enabled else None
            ),
            "canonicalInstruction": (
                "For V3.3 use the pinned six-field story claim and "
                "CAM-calibrated plain C1717 hero with exact Native Master. "
                "An old operator note saying V3.1 ONLY is historical metadata, "
                "not an authorization to override the active V3.3 contract."
                if story_enabled else None
            ),
            "savedHeroAspectRatios": [
                ratio for ratio in ("9:16", "16:9")
                if isinstance(
                    (job.output_receipts or {}).get("hero_sources", {}).get(ratio), dict
                ) and (job.output_receipts or {}).get("hero_sources", {}).get(
                    ratio, {}
                ).get("storageVerified")
            ] if story_enabled else [],
            "storyHeroUploadPaths": {
                "9:16": f"/api/v1/muse-bridge/jobs/{lid}/hero/vertical",
                "16:9": f"/api/v1/muse-bridge/jobs/{lid}/hero/horizontal",
            } if story_enabled else None,
            "calibratedHeroUploadPaths": {
                "9:16": f"/api/v1/muse-bridge/jobs/{lid}/hero/vertical/calibrated",
                "16:9": f"/api/v1/muse-bridge/jobs/{lid}/hero/horizontal/calibrated",
            } if story_enabled else None,
            "heroConstructionPolicy": (
                "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"
                if story_enabled and _deterministic_hero_required() else None
            ),
            "deterministicPrintPlacementRequired": bool(
                story_enabled and _deterministic_hero_required()
            ),
            "heroCalibrationHeader": "X-CAM-Hero-Geometry" if story_enabled else None,
            "placementDecisionPath": (
                "/api/v1/muse-bridge/policy-decisions/print-placement"
                if story_enabled else None
            ),
            "heroMaxCalibrationUncertaintyMm": 2.5 if story_enabled else None,
            "storyPilotSelected": job.listing_key in _story_v33_pilot_listings(),
            "storyCapabilityRequired": story_enabled,
            "storyPlacementPreflightRequired": story_enabled,
            "placementPreflightPath": (
                f"/api/v1/muse-bridge/jobs/{lid}/placement/preflight"
                if story_enabled else None
            ),
            "targetDurationSeconds": STORY_DURATION_SECONDS if story_enabled else 12,
            **scope,
            "attempts": cmd.attempts,
            "createdAt": cmd.created_at.isoformat() if cmd.created_at else None,
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
        if story_enabled and story_candidate.get("creativePolicy") == LITE_POLICY:
            items[-1].update({
                "creativePolicy": LITE_POLICY,
                "storyActionContract": None,
                "placementProofContract": None,
                "actionStoryRequired": False,
                "actionStoryPlan": None,
                "printPlacement": {"requiredMeasurement": False, "visualReferenceOnly": True},
                "placementPolicyDecisionPath": None,
                "canonicalInstruction": lite_worker_policy()["instructions"],
                "heroConstructionPolicy": None,
                "deterministicPrintPlacementRequired": False,
                "heroCalibrationHeader": None,
                "heroMaxCalibrationUncertaintyMm": None,
                "storyPlacementPreflightRequired": False,
                "placementPreflightPath": None,
                "placementDecisionPath": None,
                "targetDurationSeconds": LITE_TARGET_SECONDS,
                "heroUploadRequired": False,
            })
    # Oldest eligible command first within each workflow-phase priority.
    # Sorting by numeric Etsy listing ID previously let newly queued listings
    # leapfrog video jobs that had already waited hours.
    queued_at_by_command = {
        cmd.command_id: _aware(cmd.created_at) for cmd in candidates
    }
    global_story_enabled = _story_v33_enabled()
    items.sort(key=lambda row: (
        # On approved bulk rollout, finish a partially saved listing first,
        # then prefer fresh V3.3 jobs; previously claimed/partial V3.1
        # remains eligible without converting or regenerating any MP4.
        (0 if row["savedVideoAspectRatios"]
         else 1 if row["nextStep"] == "create_video" and row["storyEnabled"]
         else 2 if row["nextStep"] == "create_video" else 3)
        if _story_v33_bulk_enabled() else (
            (0 if row["nextStep"] == "create_video"
               and not row["storyPilotSelected"] else
             1 if row["nextStep"] == "create_video" else 2)
            if _continuous_gen_enabled() else (
                0 if (row["storyPilotSelected"] and row["storyEnabled"]) else
                2 if (row["storyPilotSelected"] and not global_story_enabled) else
                1 if row["nextStep"] == "create_video" else 3
            )
        ),
        queued_at_by_command[row["commandId"]],
        row["attempts"],
        row["listingId"],
    ))
    if smart_queue_enabled():
        items = _fair_muse_discovery_order(items, tenant, db)
    items = _limit_half_open_video_candidates(items, circuit)[:limit]
    pilot_readiness = _pilot_dispatch_readiness(tenant, db, by_listing)
    if request is not None:
        items = _bind_muse_claim_worker({"items": items}, request)["items"]
    elif smart_queue_enabled() and view != "full":
        items = [_compact_discovery_item(item) for item in items]
    return {
        "ok": True, "items": items, "count": len(items),
        "queueView": "compact" if smart_queue_enabled() and view != "full" else "full",
        "pilotDispatchReadiness": pilot_readiness,
        "uncommandedPilotCount": sum(
            item["needsOperatorCommand"] for item in pilot_readiness
        ),
        "remainingWithoutBothVideos": remaining,
        "videoAspectPhase": _video_aspect_phase(),
        "requiredVideoAspectRatios": _phase_required_aspects(),
        "remainingWithoutVerticalVideo": sum(
            1 for job in all_jobs if job.source_state != "missing_source"
            and "9:16" not in _verified_video_ratios(job)),
        "deferredHorizontalOnlyCount": sum(
            1 for job in all_jobs if _verified_video_ratios(job) == {"9:16"}
            and _video_aspect_phase() == "vertical_first"),
        "rendererCircuit": circuit,
        "inventoryTotal": inventory_total,
        "inventoryScanned": len(all_jobs),
        "remainingCountIsPartial": inventory_total > len(all_jobs),
        "bridgeVersion": BRIDGE_VERSION,
        "storyV33SpecAvailable": True,
        "storyV33Enabled": _story_v33_enabled(),
        "storyV33BulkEnabled": _story_v33_bulk_enabled(),
        "storyV33DefaultForNewEligibleJobs": _story_v33_bulk_enabled(),
        "storyV33RolloutMode": (
            "bulk" if _story_v33_bulk_enabled() else
            "pilot_only" if _story_v33_enabled() else "disabled"
        ),
        "storyV33PilotConfiguredCount": len(_story_v33_pilot_listings()),
        "storyLiteEnabled": _story_lite_enabled(),
        "storyLitePolicy": LITE_POLICY if _story_lite_enabled() else None,
        "storyV33TargetDurationSeconds": LITE_TARGET_SECONDS if _story_lite_enabled() else STORY_DURATION_SECONDS,
        "storyV33OutputContract": LITE_OUTPUT if _story_lite_enabled() else STORY_OUTPUT_CONTRACT,
        "storyV33SourceContract": LITE_SOURCE if _story_lite_enabled() else STORY_SOURCE_CONTRACT,
        "storyV33RequiresTwoOriginalHeroImages": not _story_lite_enabled(),
        "storyV33PlainHeroNativeMasterCompositeRequired": _deterministic_hero_required(),
        "storyV33HeroConstructionVersion": "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1",
        "storyV33MaxSourceCalibrationUncertaintyMm": 2.5,
        "storyV33RequiresFaceVisibleMinSeconds": None if _story_lite_enabled() else 8,
        "storyV33NativeRendererVerified": False,
        "continuousGenerationEnabled": _continuous_gen_enabled(),
        "continuousPrintGapToleranceMm": _placement_tolerance_mm(),
        "storyV33PrintSurfaceConformityQaRequired": not _story_lite_enabled(),
        "storyV33PrintSurfaceTrackingVerified": False,
        "storyV4FabricPilotEnabled": _v4_fabric_pilot_enabled(),
        "storyV4FabricCompositorContract": "CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1",
        "storyV4FabricTestModeProofOnly": True,
        "storyV4FabricActualOutputVerifiedWithRealPerson": False,
        "storyV4LiveHumanVideoPassedQa": False,
        "storyV33MaxNativeTakeSeconds": STORY_MAX_NATIVE_TAKE_SECONDS,
        "storyV33TwoTakeAssemblyVerified": False,
        "pollAfterSeconds": 20,
        "nextJobDelaySeconds": [2, 5],
        "pollAfterSecondsAppliesWhenIdle": True,
        "longPollPath": "/api/v1/muse-bridge/commands/wait?waitSeconds=20",
        "requiresMuseWorker": True,
        "needsComparativeArtworkQA": True,
        "motionPolicy": {
            "printPlacementPolicy": None if _story_lite_enabled() else PRINT_ANCHOR_POLICY,
            "printBoxPolicy": None if _story_lite_enabled() else PRINT_BOX_POLICY,
            "topmostFlatInkBelowLowerCollarMm": None if _story_lite_enabled() else PRINT_INK_CLEARANCE_MM,
            "decorationMethod": "flat_ink_print_only",
            "rejectPrintPositionDrift": not _story_lite_enabled(),
            "scope": "default_new_jobs; existing pinned contracts remain authoritative",
            "defaultMotionPromptVersion": (
                STORY_VERSION if _story_v33_enabled()
                else "CAM_NATURAL_EVERYDAY_MOTION_V3_1"
            ),
            "storyVersion": STORY_VERSION,
            "storyOutputDurationSeconds": LITE_TARGET_SECONDS if _story_lite_enabled() else STORY_DURATION_SECONDS,
            "storyNativeRenderMaxSecondsPerTake": STORY_MAX_NATIVE_TAKE_SECONDS,
            "storyPreferredAssembly": (
                "one_native_clip_preferred" if _story_lite_enabled()
                else "two_genuine_motion_shots_motivated_cut"
            ),
            "storyRolloutEnabled": _story_v33_enabled(),
            "storyClaimOptIn": "ClaimRequest.storyVersion+storyOutputContract+storySourceContract",
            "storySourceContract": LITE_SOURCE if _story_lite_enabled() else STORY_SOURCE_CONTRACT,
            "storyOutputContract": LITE_OUTPUT if _story_lite_enabled() else STORY_OUTPUT_CONTRACT,
            "pilotMotionPromptVersion": "CAM_NATURAL_EVERYDAY_MOTION_V3_2",
            "pilotSizeNewListings": 3,
            "pilotManagedBy": "existing CAM Auto Video scheduled task in Muse",
            "afterPilot": (
                "V3.3 default for newly eligible jobs; existing partial V3.1 "
                "continues without regeneration"
                if _story_v33_bulk_enabled() else
                "V3.3 pilot only; other eligible jobs continue V3.1"
            ),
            "pilotSpecInJob": "videoSpec.pilotMotionSpecV32",
            "motionVersionUploadHeader": "X-CAM-Motion-Version",
        },
        "outputDestination": "CAM managed private Google Drive listing/design/video_ugc",
    }


@router.get("/jobs/{listing_id}/video-status")
def read_muse_video_status(
    listing_id: str, response: Response,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Read-only receipt recovery, including after lease expiry or submission."""
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    job = db.get(CamMuseJob, (tenant, listing_id))
    if job is None:
        raise HTTPException(404, "Listing not found")
    response.headers["Cache-Control"] = "private, no-store"
    receipts = job.output_receipts or {}
    ratios = _verified_video_ratios(job)
    uploads = receipts.get("drive_uploads") or {}
    return {
        "ok": True, "listingId": listing_id, "status": job.status,
        "artworkSha256": job.artwork_sha256,
        "savedVideoAspectRatios": sorted(ratios),
        **_aspect_work_view(job),
        "missingVideoAspectRatios": [r for r in VIDEO_ASPECTS if r not in ratios],
        "activeMissingVideoAspectRatios": _aspect_work_view(job)["missingVideoAspectRatios"],
        "videos": [{"aspectRatio": r, "sha256": uploads[r]["sha256"],
                    "storageVerified": True} for r in sorted(ratios)],
        "camLinked": bool(receipts.get("camLinked")),
        "videoContentQa": receipts.get("videoContentQa", "pending_review") if ratios else None,
        "neverRegenerateSavedRatios": True,
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
        asset = _review_native_asset(db, tenant, row)
        if asset is None:
            continue
        items.append({
            "listingId": row.listing_key, "title": row.title,
            "keyword": row.keyword, "artworkSha256": row.artwork_sha256,
            "sourceImageUrl": row.product_image_url,
            "artworkUrl": f"/api/v1/muse-bridge/reviews/{row.listing_key}/artwork",
            "videoGenerationEnabled": False,
            "requiresComparativeVisualQA": True,
            "placementReview": placement_review.review_contract(
                asset, row.artwork_sha256, row.product_image_url),
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
    found = _review_native_asset(db, tenant, row)
    if found is None:
        raise HTTPException(404, "Native Master SHA not found in CAM")
    job_id, asset_id = found.locator
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
    listing_id: str, payload: VisualReviewVerdict, request: Request,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Muse-only visual QA canary; preserves rights/print QA as unverified."""
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    _assert_active_job_command_owner(
        request, db, tenant, listing_id, "review_artwork")
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
    found = _review_native_asset(db, tenant, row)
    if found is None:
        raise HTTPException(409, "CAM Native Master SHA not validated")
    if found.position == "unknown":
        required = placement_review.review_contract(
            found, row.artwork_sha256, row.product_image_url)
        if (required is None or payload.placementReviewId != required["placementReviewId"]
                or payload.observedPlacement is None):
            raise HTTPException(422, "Read current placementReview and supply bound observedPlacement")
        if payload.verdict == "pass" and payload.observedPlacement != "front_center":
            raise HTTPException(422, "Unknown placement requires a visually verified centered front print")
    elif payload.verdict == "pass" and payload.observedPlacement not in (None, "front_center"):
        raise HTTPException(422, "Observed placement contradicts front master")
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
    if found.position == "unknown":
        review["placement_review"] = {
            "policy": placement_review.POLICY,
            "placementReviewId": payload.placementReviewId,
            "observedPlacement": payload.observedPlacement,
            "jobId": found.job_id, "assetId": found.asset_id,
            "evidence": "worker_reported_comparative_visual_review",
        }
    row.output_receipts = {"visual_qa": review}
    if payload.verdict == "pass":
        job_id, asset_id = found.locator
        row.artwork_asset_path = (
            f"/api/v1/etsy-spy/image-agents/designs/{job_id}/{asset_id}/original"
        )
        row.source_state = "ready_for_muse"
        row.status = "pending"
    row.updated_at = _utc()
    db.commit()
    request.state.muse_business_result = {"qaVerdict": payload.verdict, "jobStatus": row.status}
    return {"ok": True, "listingId": listing_id, "status": row.status,
            "visualQa": payload.verdict, "rightsVerified": False,
            "printReady": False, "videoGenerated": False}


@router.post("/jobs/{listing_id}/claim")
def claim(listing_id: str, payload: ClaimRequest, request: Request,
          x_cam_muse_policy_version: str | None = Header(default=None),
          tenant: str = Depends(_muse), db: Session = Depends(get_db)):
    _assert_current_muse_policy(x_cam_muse_policy_version)
    _block_legacy_claims_during_cutover()
    _assert_muse_executor_ready(request, existing_command_job=True)
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    if _muse_dual_enabled() and payload.worker != _muse_identity(request):
        raise HTTPException(403, "Muse claim worker ID must match its token")
    lite_requested = payload.storyOutputContract == LITE_OUTPUT
    if payload.storyVersion == STORY_VERSION and payload.storyOutputContract not in (
            STORY_OUTPUT_CONTRACT, LITE_OUTPUT):
        raise HTTPException(409, "Use the exact listing videoJobClaim.body output contract")
    if payload.storyVersion is None and payload.storyOutputContract is not None:
        raise HTTPException(409, "Story output contract requires matching V3.3 version")
    if payload.storyVersion == STORY_VERSION and payload.storySourceContract != (
            LITE_SOURCE if lite_requested else STORY_SOURCE_CONTRACT):
        raise HTTPException(409, "Use the exact listing videoJobClaim.body source contract")
    if payload.storyVersion is None and payload.storySourceContract is not None:
        raise HTTPException(409, "Source contract requires matching V3.3 version")
    if payload.storyVersion == STORY_VERSION and not lite_requested and (
            payload.storyActionContract != STORY_ACTION_VERSION
            or payload.placementProofContract != PLACEMENT_PROOF_VERSION):
        raise HTTPException(409, "V3.3 claim requires story-action and 150mm placement acknowledgements")
    _assert_active_job_command_owner(request, db, tenant, listing_id, "create_video")
    # A previously claimed command is permitted to finish. Do not
    # independently reopen renderer traffic through a direct legacy claim.
    existing = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.listing_key == listing_id,
        CamMuseCommand.status == "claimed",
        CamMuseCommand.phase == "create_video",
        CamMuseCommand.lease_expires_at > _utc(),
    ).order_by(CamMuseCommand.created_at.desc()).limit(1).with_for_update())
    required = _pinned_work_aspects(existing.details) if existing else _phase_required_aspects()
    if smart_queue_enabled() and existing is None:
        circuit = renderer_circuit(db, tenant)
        if circuit["open"]:
            raise HTTPException(409, {"code": "RENDERER_CIRCUIT_OPEN", **circuit})
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
             attempts=CamMuseJob.attempts + 1, updated_at=now
    ).execution_options(synchronize_session=False)
    # Atomic DB predicate is authoritative. Do not re-evaluate timezone
    # comparisons on any stale ORM objects in a long-lived test/session.
    changed = db.execute(statement).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "Unavailable or artwork QA not passed")
    # The atomic UPDATE intentionally disables ORM synchronization. Reload
    # even if this session already inspected the pending listing.
    row = db.get(CamMuseJob, (tenant, listing_id), populate_existing=True)
    if existing is None:
        from .regeneration import requested_aspects
        required = requested_aspects(row) or required
    if not (set(required) - _verified_video_ratios(row)):
        db.rollback()
        raise HTTPException(409, "Required video aspects already verified; do not regenerate")
    if (isinstance(row.output_receipts, dict)
            and row.output_receipts.get("videoCapabilityHold")):
        db.rollback()
        raise HTTPException(409, "Renderer capability hold requires independent canary verification")
    retry_wait = _render_retry_wait_seconds(row.output_receipts)
    if retry_wait:
        db.rollback()
        raise HTTPException(409, {
            "code": "RENDERER_TRANSIENT_RETRY_DEFERRED",
            "retryAfterSeconds": retry_wait,
            "continueOtherListings": True,
        })
    if _automation_soft_hold_active(row.output_receipts):
        db.rollback()
        raise HTTPException(409, "Listing is on temporary operator HOLD")
    # Opt in only when the Muse worker advertises the genuine 12-second
    # story workflow (up to 10s native per source take). Pin in one
    # transaction; retries cannot
    # switch a partially uploaded V3.1 job into an incompatible story.
    if payload.storyVersion == STORY_VERSION:
        receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
        pinned = _pinned_story_v33(row)
        fresh = (
            _story_v33_allowed_for_job(row) and 1 <= row.attempts <= 5
            and not receipts.get("drive_uploads") and not receipts.get("videos")
            and not receipts.get("hero_sources") and not receipts.get("story_action_v1")
            and row.artwork_sha256 and row.source_state == "ready_for_muse"
        )
        if not pinned and not fresh:
            db.rollback()
            raise HTTPException(409, "Story V3.3 not enabled or listing is legacy/in-flight")
        if lite_requested:
            if not _story_lite_pin(pinned):
                if (not _story_lite_enabled() or not _story_v33_allowed_for_job(row)
                        or receipts.get("drive_uploads") or receipts.get("videos")
                        or receipts.get("story_action_v1") or not (pinned or fresh)):
                    db.rollback()
                    raise HTTPException(409, "Lite is only for new or unrendered V3.3 work")
                if pinned:
                    receipts["previousStrictStoryContractV33"] = pinned
                base = _build_job_story_spec(row)
                plans = (pinned or {}).get("heroGenerationPlans") or {
                    ratio: {field: plan[field] for field in (
                        "heroRequestId", "heroSeed", "storyId",
                    )}
                    for ratio, plan in base["aspectPlans"].items()
                }
                receipts["story_contract_v33"] = {
                    "storyVersion": STORY_VERSION,
                    "storyOutputContract": LITE_OUTPUT,
                    "storySourceContract": LITE_SOURCE,
                    "creativePolicy": LITE_POLICY,
                    "garmentSku": "C1717",
                    "artworkSha256": row.artwork_sha256,
                    "targetDurationSeconds": LITE_TARGET_SECONDS,
                    "assemblyMode": "one_native_clip_preferred",
                    "heroGenerationPlans": plans,
                }
                row.output_receipts = receipts
        elif _story_lite_pin(pinned):
            db.rollback()
            raise HTTPException(409, "Listing is pinned to Story Lite; use returned claim body")
        elif fresh and not pinned:
            receipts["story_contract_v33"] = {
                "storyVersion": STORY_VERSION,
                "storyOutputContract": STORY_OUTPUT_CONTRACT,
                "storySourceContract": STORY_SOURCE_CONTRACT,
                "garmentSku": "C1717",
                "artworkSha256": row.artwork_sha256,
                "targetDurationSeconds": STORY_DURATION_SECONDS,
                "assemblyMode": "two_genuine_motion_shots_motivated_cut",
                "maxNativeTakeSeconds": STORY_MAX_NATIVE_TAKE_SECONDS,
                "heroGenerationPlans": {
                    ratio: {
                        "heroRequestId": plan["heroRequestId"],
                        "heroSeed": plan["heroSeed"],
                        "storyId": plan["storyId"],
                    }
                    for ratio, plan in _build_job_story_spec(row)["aspectPlans"].items()
                },
            }
            row.output_receipts = receipts
    elif _pinned_story_v33(row):
        db.rollback()
        raise HTTPException(
            409, "This listing is already pinned to V3.3: send the exact "
            "five-field story claim from /commands[].videoJobClaim.body. "
            "Never switch a partly completed V3.3 listing to legacy mode.",
        )
    elif _story_v33_allowed_for_job(row):
        receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
        if (row.artwork_sha256 and row.source_state == "ready_for_muse"
                and not receipts.get("drive_uploads") and not receipts.get("videos")
                and not receipts.get("hero_sources")
                and not receipts.get("story_action_v1")):
            # A legacy worker cannot silently generate another 12-second,
            # motionless clip after V3.3 rollout is explicitly enabled.
            db.rollback()
            raise HTTPException(
                409,
                "V3.3 claim body required for fresh story work: read "
                "/commands[].videoJobClaim.body (or /video-work[].videoJobClaim.body) "
                "and POST the five story/placement fields with your authorized "
                "worker ID. Do not retry with a V3.1 body.",
            )
    if payload.storyVersion is None:
        receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
        existing_action = _pinned_action_story_v1(row)
        # After the atomic claim above, row.attempts has incremented.
        # An expired V3.1 retry without any saved media may adopt the
        # action-story policy. Never convert a V3.3 pin or even one saved
        # legacy video aspect, and never take over an active lease.
        fresh_action = (
            1 <= row.attempts <= 5 and row.artwork_sha256
            and row.source_state == "ready_for_muse"
            and not _pinned_story_v33(row)
            and not _story_v33_allowed_for_job(row)
            and not receipts.get("drive_uploads") and not receipts.get("videos")
            and not receipts.get("hero_sources")
        )
        if fresh_action or existing_action:
            if (payload.storyActionContract != STORY_ACTION_VERSION
                    or payload.placementProofContract != PLACEMENT_PROOF_VERSION):
                db.rollback()
                raise HTTPException(
                    409, "STORY_ACTION_AND_150MM_PLACEMENT_ACK_REQUIRED: "
                    "Muse worker must update scheduled task before claiming a new video"
                )
            if fresh_action and not existing_action:
                try:
                    plan = action_plan(
                        title=row.title, listing_key=row.listing_key,
                        artwork_sha256=row.artwork_sha256,
                    )
                except ValueError as exc:
                    db.rollback()
                    raise HTTPException(422, "Approved C1717 action story is unavailable") from exc
                receipts["story_action_v1"] = {
                    "policyVersion": STORY_ACTION_VERSION,
                    "placementPolicyVersion": PLACEMENT_PROOF_VERSION,
                    "artworkSha256": row.artwork_sha256,
                    "garmentSku": GARMENT_SKU,
                    "storyId": plan["storyId"],
                    "requiredActualActionSeconds": 6,
                    "firstInkBelowLowerCollarMm": PLACEMENT_TARGET_MM,
                    "pinnedAt": _utc().isoformat(),
                }
                row.output_receipts = receipts
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    receipts["videoWorkScope"] = {"requiredVideoAspectRatios": required,
                                  "commandId": existing.command_id if existing else None}
    if _muse_dual_enabled():
        receipts["museWorkerOwner"] = _muse_identity(request)
    row.output_receipts = receipts
    db.commit()
    return {"lease": lease, "leaseExpiresAt": row.lease_expires_at,
            "job": _row_view(row), "worker": payload.worker}

@router.post("/jobs/{listing_id}/placement/preflight")
def submit_c1717_placement_preflight(
    listing_id: str, payload: SourcePlacementPreflight, request: Request,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Worker-side calibrated C1717 SOURCE proof, not independent image QA.

    Bind a front-facing start-frame SHA and a 150mm physical-scale measurement
    to the exact job lease and artwork. Never silently accept uncalibrated
    coordinates or claim an image actually measures 150mm without a
    same-fabric-plane physical reference. Do not store an extra database.
    """
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404)
    row = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if (row is None or row.status != "claimed"
            or not row.lease_hash or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                row.lease_hash, hashlib.sha256(payload.lease.encode()).hexdigest()
            )):
        raise HTTPException(409, "Valid Muse video job lease is required")
    _assert_muse_video_owner(request, row)
    action_pin = _pinned_action_story_v1(row)
    story_pin = _pinned_story_v33(row)
    if not action_pin and not story_pin:
        raise HTTPException(409, "Pinned C1717 action or V3.3 story required for placement preflight")
    if story_pin:
        # V3.3 proves the measurement on the independently generated, Drive-
        # verified hero for THIS ratio; never accept an arbitrary source SHA.
        existing = row.output_receipts if isinstance(row.output_receipts, dict) else {}
        source_hero = (existing.get("hero_sources") or {}).get(payload.aspectRatio)
        expected_plan = (story_pin.get("heroGenerationPlans") or {}).get(payload.aspectRatio)
        if (not isinstance(source_hero, dict) or not source_hero.get("storageVerified")
                or not isinstance(expected_plan, dict)
                or source_hero.get("sha256") != payload.sourceFrameSha256
                or source_hero.get("heroRequestId") != expected_plan.get("heroRequestId")
                or source_hero.get("heroSeed") != expected_plan.get("heroSeed")
                or source_hero.get("width") != payload.sourceWidthPx
                or source_hero.get("height") != payload.sourceHeightPx):
            raise HTTPException(409, "V3.3 preflight requires matching Drive-verified ratio hero")
        if _deterministic_hero_required():
            from .calibrated_hero import COMPOSITOR_VERSION
            proof = source_hero.get("calibratedComposite") or {}
            if (not isinstance(proof, dict)
                    or proof.get("policyVersion") != COMPOSITOR_VERSION
                    or proof.get("renderedHeroSha256") != payload.sourceFrameSha256
                    or proof.get("nativeMasterSha256") != row.artwork_sha256
                    or not isinstance(proof.get("maximumEstimatedPlacementErrorMm"), (float, int))
                    or proof["maximumEstimatedPlacementErrorMm"] > PLACEMENT_TOLERANCE_MM):
                raise HTTPException(
                    409,
                    "V3.3 requires a CAM-calibrated plain-shirt + Native Master "
                    "hero; do not re-prompt an AI to move the finished print",
                )
            comparisons = (
                (proof.get("lowerCollarBottomYpx"), payload.lowerCollarBottomYpx, 0.51),
                (proof.get("firstInkTopYpx"), payload.firstInkTopYpx, 0.51),
                (proof.get("garmentReferenceWidthPx"), payload.garmentReferenceWidthPx, 0.51),
                (proof.get("garmentReferenceWidthMm"), payload.garmentReferenceWidthMm, 0.05),
            )
            if (any(not isinstance(a, (int, float)) or abs(a - b) > delta
                    for a, b, delta in comparisons)
                    or proof.get("calibrationMethod") != payload.calibrationMethod):
                raise HTTPException(409, "Preflight must use CAM-computed hero calibration, not re-estimated print placement")
    if payload.artworkSha256 != row.artwork_sha256:
        raise HTTPException(409, "Approved Native Master SHA mismatch")
    if not all((
        payload.c1717ShortSleeveSourceVerified,
        payload.nativeMasterMatchedToSource,
        payload.printFullyVisible,
        payload.storyActionPlanAcknowledged,
        payload.preRenderSourceImage,
    )):
        raise HTTPException(422, "Source hero garment, artwork and story checks must be true")
    if (payload.calibrationMethod == "visual_estimate_unverified"
            and not _continuous_gen_enabled()):
        raise HTTPException(422, "Visual estimate may not be submitted as measured physical placement")
    if (payload.lowerCollarBottomYpx >= payload.firstInkTopYpx
            or payload.firstInkTopYpx >= payload.sourceHeightPx
            or payload.garmentReferenceWidthPx >= payload.sourceWidthPx):
        raise HTTPException(422, "Source print geometry is invalid")
    measured_mm = (
        (payload.firstInkTopYpx - payload.lowerCollarBottomYpx)
        * payload.garmentReferenceWidthMm / payload.garmentReferenceWidthPx
    )
    if abs(measured_mm - PLACEMENT_TARGET_MM) > _placement_tolerance_mm():
        raise HTTPException(
            422, "C1717 print appears outside the permitted 130-170mm "
            "visual estimate band" if _continuous_gen_enabled()
            else "C1717 placement outside 150mm calibrated source tolerance",
        )
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    uploads = receipts.get("drive_uploads") or {}
    if payload.aspectRatio in uploads:
        raise HTTPException(409, "Cannot change placement proof after video upload")
    proofs = dict(receipts.get("story_action_preflight") or {})
    proof = {
        "policyVersion": PLACEMENT_PROOF_VERSION,
        "storyActionVersion": STORY_ACTION_VERSION,
        "storyVersion": STORY_VERSION if story_pin else None,
        "storySourceContract": STORY_SOURCE_CONTRACT if story_pin else None,
        "artworkSha256": payload.artworkSha256,
        "sourceFrameSha256": payload.sourceFrameSha256,
        "sourceImageWidthPx": payload.sourceWidthPx,
        "sourceImageHeightPx": payload.sourceHeightPx,
        "calibrationMethod": payload.calibrationMethod,
        "measuredShirtReferenceWidthPx": payload.garmentReferenceWidthPx,
        "measuredShirtReferenceWidthMm": payload.garmentReferenceWidthMm,
        "lowerCollarBottomYpx": payload.lowerCollarBottomYpx,
        "firstInkTopYpx": payload.firstInkTopYpx,
        "workerCalculatedGapMm": round(measured_mm, 2),
        "placementGateMode": (
            "VISUAL_ESTIMATE_130_170_PENDING_QA"
            if _continuous_gen_enabled() else "MEASURED_145_155"
        ),
        "physicalMeasurementVerified": False,
        "placementMeasurementReliability": (
            "visually_estimated_not_physically_verified"
            if payload.calibrationMethod == "visual_estimate_unverified"
            else "worker_reported_not_independently_verified"
        ),
        "sourceCalibrationUncertaintyMm": (
            (source_hero.get("calibratedComposite") or {}).get("calibrationUncertaintyMm")
            if story_pin else None
        ),
        "maximumEstimatedPlacementErrorMm": (
            (source_hero.get("calibratedComposite") or {}).get(
                "maximumEstimatedPlacementErrorMm")
            if story_pin else None
        ),
        "targetMm": PLACEMENT_TARGET_MM,
        "workerReportedC1717AndNativeMasterCheck": True,
        "verificationStatus": "worker_reported_pending_independent_visual_review",
    }
    previous = proofs.get(payload.aspectRatio)
    if previous is not None:
        if (previous.get("sourceFrameSha256") != payload.sourceFrameSha256
                or previous.get("workerCalculatedGapMm") != proof["workerCalculatedGapMm"]):
            raise HTTPException(409, "Cannot replace a pinned 150mm placement source")
        return {"ok": True, "reused": True,
                "aspectRatio": payload.aspectRatio,
                "workerMeasuredGapMm": proof["workerCalculatedGapMm"],
                "sourceFrameSha256": payload.sourceFrameSha256,
                "independentlyVerified": False}
    proof["reportedAt"] = _utc().isoformat()
    proofs[payload.aspectRatio] = proof
    receipts["story_action_preflight"] = proofs
    row.output_receipts = receipts
    row.updated_at = _utc()
    db.commit()
    return {"ok": True, "reused": False,
            "aspectRatio": payload.aspectRatio,
            "workerMeasuredGapMm": proof["workerCalculatedGapMm"],
            "sourceFrameSha256": payload.sourceFrameSha256,
            "independentlyVerified": False,
            "nextStep": "render two genuine moving takes; source physical placement subject to video QA"}


@router.get("/jobs/{listing_id}/story-contract")
def read_muse_story_contract(
    listing_id: str, response: Response, request: Request,
    aspect: Literal["vertical", "horizontal"] | None = None,
    full: bool = False,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Recover a pinned V3.3 contract after a truncated job-claim response.

    A claimed job is intentionally hidden from pending-list endpoints. This
    remains tenant-scoped, authenticated, read-only and lease-independent.
    Pinned heroRequestId/seed must win over any later prompt version.
    """
    response.headers["Cache-Control"] = "private, no-store"
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Invalid listing ID")
    job = db.get(CamMuseJob, (tenant, listing_id))
    if job is None:
        raise HTTPException(404, "Unknown CAM listing")
    pin = _pinned_story_v33(job)
    story = _story_candidate_v33(job)
    if not story or not (pin or _story_v33_allowed_for_job(job)):
        raise HTTPException(409, "This listing is not on the V3.3 story workflow")
    pinned_plans = pin.get("heroGenerationPlans") if pin else None
    if isinstance(pinned_plans, dict):
        plans = story.get("aspectPlans") or {}
        for ratio in ("9:16", "16:9"):
            plan = plans.get(ratio)
            fixed = pinned_plans.get(ratio)
            if not isinstance(plan, dict) or not isinstance(fixed, dict):
                raise HTTPException(409, "Pinned hero aspect plan is incomplete")
            for field in ("heroRequestId", "heroSeed", "storyId"):
                if field not in fixed:
                    raise HTTPException(409, "Pinned hero metadata is incomplete")
                plan[field] = fixed[field]
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    claim = _video_job_claim_contract(job)
    # Avoid sending the same story twice in nested claim payloads.
    claim.pop("storySpecV33", None)
    worker_id = _muse_identity(request)
    claim["body"] = {
        "worker": "muse" if worker_id == "legacy" else worker_id,
        **claim["body"],
    }
    return {
        "ok": True,
        "listingId": listing_id,
        "storyVersion": STORY_VERSION,
        "storyPinned": bool(pin),
        "storySpecV33": (
            story if full else _compact_story_spec(
                story, include_prompt=True,
                aspect={"vertical": "9:16", "horizontal": "16:9"}.get(aspect),
            )
        ),
        "storySpecMode": "full" if full else "compact",
        "pinnedHeroGenerationPlans": pinned_plans,
        "heroPlanAuthority": "stored_cam_pin" if pin else "candidate_not_pinned_yet",
        "videoJobClaim": claim,
        "savedHeroAspectRatios": [
            aspect for aspect in ("9:16", "16:9")
            if isinstance((receipts.get("hero_sources") or {}).get(aspect), dict)
            and receipts["hero_sources"][aspect].get("storageVerified") is True
        ],
        "savedVideoAspectRatios": sorted(_verified_video_ratios(job)),
        "missingVideoAspectRatios": claim["missingVideoAspectRatios"],
        "jobStatus": job.status,
        "readOnlyNoClaimNoLease": True,
        "neverRegenerateSavedVideoRatios": True,
    }


@router.get("/jobs/{listing_id}/artwork")
async def artwork(listing_id: str, request: Request, tenant: str = Depends(_muse),
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
    _assert_muse_video_owner(request, row)
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
def submit(listing_id: str, payload: SubmitRequest, request: Request,
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
    _assert_muse_video_owner(request, row)
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    if _pinned_story_v33(row):
        raise HTTPException(409, "Story videos require native MP4 upload and independent video QA")
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
    "/jobs/{listing_id}/hero/{aspect}/calibrated",
    openapi_extra={
        "requestBody": {"required": True, "content": {
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        }},
    },
)
async def upload_native_master_calibrated_hero(
    listing_id: str, aspect: Literal["vertical", "horizontal"],
    request: Request,
    x_muse_lease: str | None = Header(default=None),
    x_cam_story_source_contract: Literal["CAM_STORY_DUAL_HERO_FACE_V1"] | None = Header(default=None),
    x_cam_hero_request_id: str | None = Header(default=None),
    x_cam_hero_seed: int | None = Header(default=None),
    x_cam_hero_geometry: str | None = Header(default=None),
    x_cam_previous_hero_sha256: str | None = Header(default=None),
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Deterministically apply exact CAM Native Master to a measured PLAIN C1717 hero.

    The worker sends an independently generated blank-shirt photo (not a
    printed photo or Etsy mockup). CAM overlays the original transparent
    Native Master using physical-plane scale; no AI redraw, no new database.
    Existing Drive-verified heroes remain available as audit history on a
    bounded repair. This route is NOT independent source or video QA.
    """
    from .hero_storage import (
        MAX_HERO_BYTES, heroes_are_distinct, store_muse_hero,
    )
    from .calibrated_hero import (
        COMPOSITOR_VERSION, composite_native_master_on_plain_hero,
    )
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    lease = x_muse_lease or ""
    ctype = request.headers.get("content-type", "").split(";")[0].lower()
    if (not 32 <= len(lease) <= 120 or ctype not in ("image/jpeg", "image/png")
            or x_cam_story_source_contract != STORY_SOURCE_CONTRACT):
        raise HTTPException(422, "Leased V3.3 plain C1717 JPEG/PNG hero required")
    if not x_cam_hero_geometry or len(x_cam_hero_geometry) > 2000:
        raise HTTPException(422, "Measured C1717 fabric-plane geometry header required")
    try:
        geometry = CalibratedHeroGeometry.model_validate_json(x_cam_hero_geometry)
    except ValidationError as exc:
        # Never echo submitted calibration values or arbitrary image metadata.
        # Muse needs the *field names* to fix schema typos, not another blind
        # attempt at generating or moving text in an image.
        issues = [
            {
                "field": ".".join(str(segment) for segment in err.get("loc", ())) or "root",
                "reason": err.get("type", "invalid"),
            }
            for err in exc.errors(include_input=False, include_context=False)[:16]
        ]
        raise HTTPException(422, {
            "code": "CALIBRATED_HERO_GEOMETRY_SCHEMA_INVALID",
            "message": "Fix the named JSON fields and retry the same blank hero; never guess physical dimensions or relax the 150mm tolerance",
            "issues": issues,
            "schema": "GET /api/v1/muse-bridge/workers/config -> v33CalibratedHeroGeometrySchema",
        }) from exc
    except ValueError as exc:
        raise HTTPException(422, {
            "code": "CALIBRATED_HERO_GEOMETRY_JSON_INVALID",
            "message": "X-CAM-Hero-Geometry must be valid JSON",
        }) from exc
    row = db.get(CamMuseJob, (tenant, listing_id))
    if (row is None or row.status != "claimed"
            or row.source_state != "ready_for_muse"
            or not row.artwork_sha256 or not row.artwork_asset_path
            or not row.lease_hash or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                row.lease_hash, hashlib.sha256(lease.encode()).hexdigest()
            )):
        raise HTTPException(409, "Valid claimed Muse V3.3 job lease required")
    _assert_muse_video_owner(request, row)
    pin = _pinned_story_v33(row)
    spec = _story_candidate_v33(row)
    ratio = "9:16" if aspect == "vertical" else "16:9"
    if not pin or not spec:
        raise HTTPException(409, "V3.3 dual-hero claim was not pinned")
    plan = spec["aspectPlans"][ratio]
    expected = (pin.get("heroGenerationPlans") or {}).get(ratio)
    if (expected != {
            "heroRequestId": plan["heroRequestId"],
            "heroSeed": plan["heroSeed"],
            "storyId": plan["storyId"],
            } or x_cam_hero_request_id != plan["heroRequestId"]
            or x_cam_hero_seed != plan["heroSeed"]):
        raise HTTPException(409, "Unrecognized native ratio hero request/seed")
    prior_receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
    previous = (prior_receipts.get("hero_sources") or {}).get(ratio)
    revisions = (prior_receipts.get("hero_revisions") or {}).get(ratio) or []
    if (ratio in (prior_receipts.get("drive_uploads") or {})
            or ratio in (prior_receipts.get("story_action_preflight") or {})
            or len(revisions) >= 2):
        raise HTTPException(409, "Hero has a video/preflight or repair limit reached")
    if previous:
        if (not HEX64.fullmatch(x_cam_previous_hero_sha256 or "")
                or x_cam_previous_hero_sha256 != previous.get("sha256")):
            raise HTTPException(409, "Explicit previous hero SHA required for repair")
    elif x_cam_previous_hero_sha256 is not None:
        raise HTTPException(409, "Previous hero SHA provided without prior source")
    image = bytearray()
    async for part in request.stream():
        image.extend(part)
        if len(image) > MAX_HERO_BYTES:
            raise HTTPException(413, "Plain C1717 hero exceeds 12 MiB")
    if len(image) < 2048:
        raise HTTPException(422, "Empty plain-shirt source photo")

    # Resolve exactly the approved Native Master already stored for this
    # listing. No replacement art, font synthesis or inferred listing title.
    if not ARTWORK_PATH.fullmatch(row.artwork_asset_path):
        raise HTTPException(409, "Invalid approved Native Master locator")
    job_id, asset_id = row.artwork_asset_path.split("/")[6:8]
    if _stored_native_asset(db, tenant, listing_id, row.artwork_sha256) != (job_id, asset_id):
        raise HTTPException(409, "Native Master not approved for this listing/SHA")
    remote = _private_drive_design(job_id, asset_id, tenant, db)
    if remote:
        if remote.get("output_sha256") != row.artwork_sha256:
            raise HTTPException(409, "Native Master Drive SHA mismatch")
        db.rollback()  # Drop DB read transaction while making Drive network I/O.
        from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
        master_png = await read_design_from_drive(
            tenant_id=tenant,
            asset_id=job_id + ":" + asset_id + ":original",
            remote_id=str(remote.get("drive_file_id") or ""),
            sha256=row.artwork_sha256,
            max_bytes=20 * 1024 * 1024,
        )
    else:
        source, digest = _checked_design_media(job_id, asset_id, db, tenant)
        if digest != row.artwork_sha256:
            raise HTTPException(409, "Approved Native Master file digest mismatch")
        db.rollback()
        master_png = await run_in_threadpool(source.read_bytes)
    if hashlib.sha256(master_png).hexdigest() != row.artwork_sha256:
        raise HTTPException(409, "Native Master bytes failed SHA-256 verification")
    rendered, proof = await asyncio.to_thread(
        composite_native_master_on_plain_hero,
        plain_bytes=bytes(image), content_type=ctype,
        aspect=aspect, artwork_png=master_png,
        calibration=geometry.model_dump(),
    )
    from .hero_storage import validate_hero_image
    meta = validate_hero_image(rendered, aspect, "image/jpeg")
    meta["heroRequestId"] = x_cam_hero_request_id
    meta["heroSeed"] = x_cam_hero_seed
    meta["calibratedComposite"] = proof
    alternate = (prior_receipts.get("hero_sources") or {}).get(
        "16:9" if ratio == "9:16" else "9:16"
    )
    if alternate and not heroes_are_distinct(alternate, meta):
        raise HTTPException(409, "Corrected hero must remain independent by aspect")
    if previous and previous.get("sha256") == meta["sha256"]:
        return {
            "ok": True, "reused": True, "aspectRatio": ratio,
            "imageSha256": meta["sha256"], "calibration": proof,
            "storageVerified": True,
            "independentVisualQa": "pending_review",
        }
    saved = await store_muse_hero(
        tenant_id=tenant, listing_id=listing_id,
        artwork_sha256=row.artwork_sha256, aspect=aspect,
        image_bytes=rendered, metadata=meta,
    )
    db.expire_all()
    locked = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if (locked is None or locked.status != "claimed"
            or locked.artwork_sha256 != row.artwork_sha256
            or not locked.lease_hash or _aware(locked.lease_expires_at) is None
            or _aware(locked.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                locked.lease_hash, hashlib.sha256(lease.encode()).hexdigest()
            )):
        db.rollback()
        raise HTTPException(409, "Muse lease changed during calibrated hero storage")
    _assert_muse_video_owner(request, locked)
    receipts = dict(locked.output_receipts) if isinstance(locked.output_receipts, dict) else {}
    heroes = dict(receipts.get("hero_sources") or {})
    current = heroes.get(ratio)
    current_revisions = (receipts.get("hero_revisions") or {}).get(ratio) or []
    if (ratio in (receipts.get("drive_uploads") or {})
            or ratio in (receipts.get("story_action_preflight") or {})
            or len(current_revisions) >= 2
            or (current or {}).get("sha256") != (previous or {}).get("sha256")
            or (current and x_cam_previous_hero_sha256 != current.get("sha256"))):
        db.rollback()
        raise HTTPException(409, "Hero changed or was used during correction")
    counterpart = heroes.get("16:9" if ratio == "9:16" else "9:16")
    if counterpart and not heroes_are_distinct(counterpart, saved):
        db.rollback()
        raise HTTPException(409, "Corrected hero duplicates other aspect")
    if current:
        hist = dict(receipts.get("hero_revisions") or {})
        hist[ratio] = [
            *current_revisions,
            {"previousSource": current,
             "supersededBySha256": saved["sha256"],
             "reason": "calibrated_native_master_print_correction",
             "supersededAt": _utc().isoformat()},
        ]
        receipts["hero_revisions"] = hist
    heroes[ratio] = saved
    receipts["hero_sources"] = heroes
    receipts["heroSourceQa"] = "pending_independent_visual_review"
    locked.output_receipts = receipts
    locked.updated_at = _utc()
    db.commit()
    return {
        "ok": True, "reused": False, "aspectRatio": ratio,
        "imageSha256": saved["sha256"],
        "storageVerified": True, "calibration": proof,
        "heroConstruction": COMPOSITOR_VERSION,
        "oldHeroPreservedInRevisionHistory": bool(previous),
        "independentVisualQa": "pending_review",
        "nextStep": (
            "Use this exact composited image SHA and numeric geometry for "
            "ratio-specific placement preflight, then render only if accepted."
        ),
    }


@router.post(
    "/jobs/{listing_id}/hero/{aspect}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def upload_ugc_hero(
    listing_id: str, aspect: Literal["vertical", "horizontal"],
    request: Request,
    x_muse_lease: str | None = Header(default=None),
    x_cam_story_source_contract: Literal["CAM_STORY_DUAL_HERO_FACE_V1"] | None = Header(default=None),
    x_cam_hero_request_id: str | None = Header(default=None),
    x_cam_hero_seed: int | None = Header(default=None),
    x_cam_previous_hero_sha256: str | None = Header(default=None),
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Accept the ACTUAL independently generated hero image for this ratio.

    An upload is image provenance and Drive storage, NOT independent approval
    of face visibility, source originality, or correct artwork. For V3.3 only.
    """
    from .hero_storage import (
        MAX_HERO_BYTES, validate_hero_image, heroes_are_distinct, store_muse_hero,
    )
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    lease = x_muse_lease or ""
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    if not 32 <= len(lease) <= 120 or content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(422, "Lease and JPEG/PNG hero image required")
    if x_cam_story_source_contract != STORY_SOURCE_CONTRACT:
        raise HTTPException(422, "Independent dual-hero V3.3 source contract required")
    row = db.get(CamMuseJob, (tenant, listing_id))
    if (row is None or row.status != "claimed"
            or row.source_state != "ready_for_muse" or not row.artwork_sha256
            or not row.lease_hash or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                row.lease_hash, hashlib.sha256(lease.encode()).hexdigest()
            )):
        raise HTTPException(409, "Hero upload requires a valid Muse job lease")
    _assert_muse_video_owner(request, row)
    pin = _pinned_story_v33(row)
    story = _story_candidate_v33(row)
    if not pin or not story:
        raise HTTPException(409, "V3.3 dual-hero claim is not pinned")
    if _deterministic_hero_required():
        raise HTTPException(
            409,
            "V3.3 direct printed-hero upload disabled: generate a plain C1717 "
            "shirt and use /hero/{aspect}/calibrated with measured geometry",
        )
    # FastAPI resolves Header(default=None) into str/None for HTTP calls.
    # Normalize when this handler is tested directly without dependency
    # injection, rather than misreading Header metadata as a supplied SHA.
    expected_previous_sha = (
        x_cam_previous_hero_sha256
        if isinstance(x_cam_previous_hero_sha256, str) else None
    )
    ratio = "9:16" if aspect == "vertical" else "16:9"
    plan = story["aspectPlans"][ratio]
    pinned_plan = (pin.get("heroGenerationPlans") or {}).get(ratio)
    if pinned_plan != {
        "heroRequestId": plan["heroRequestId"],
        "heroSeed": plan["heroSeed"],
        "storyId": plan["storyId"],
    }:
        raise HTTPException(409, "Pinned independent hero plan differs from the active contract")
    if (x_cam_hero_request_id != plan["heroRequestId"]
            or x_cam_hero_seed != plan["heroSeed"]):
        raise HTTPException(422, "Hero source does not match the planned independent request")
    parts = bytearray()
    async for chunk in request.stream():
        parts.extend(chunk)
        if len(parts) > MAX_HERO_BYTES:
            raise HTTPException(413, "Hero source exceeds 12 MiB")
    image_bytes = bytes(parts)
    metadata = await asyncio.to_thread(
        validate_hero_image, image_bytes, aspect, content_type
    )
    receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
    prior = dict(receipts.get("hero_sources") or {})
    previous = prior.get(ratio)
    revisions = (receipts.get("hero_revisions") or {}).get(ratio) or []
    if ratio in (receipts.get("drive_uploads") or {}):
        raise HTTPException(409, "Cannot change hero source after video upload")
    if previous:
        if (previous.get("sha256") == metadata["sha256"]
                and previous.get("heroRequestId") == x_cam_hero_request_id):
            return {"ok": True, "reused": True, "aspectRatio": ratio,
                    "imageSha256": previous["sha256"],
                    "faceVisibilityQa": "pending_review"}
        if (not _continuous_gen_enabled() or len(revisions) >= 2
                or not HEX64.fullmatch(expected_previous_sha or "")
                or expected_previous_sha != previous.get("sha256")):
            raise HTTPException(
                409, "Hero repair requires continuous mode, exact "
                     "X-CAM-Previous-Hero-Sha256, and fewer than two revisions",
            )
        old_preflight = (receipts.get("story_action_preflight") or {}).get(ratio)
        if (old_preflight is not None
                and old_preflight.get("sourceFrameSha256") != previous.get("sha256")):
            raise HTTPException(409, "Existing preflight refers to another hero")
    elif expected_previous_sha is not None:
        raise HTTPException(409, "Previous hero SHA supplied without existing source")
    metadata["heroRequestId"] = x_cam_hero_request_id
    metadata["heroSeed"] = x_cam_hero_seed
    other = prior.get("16:9" if ratio == "9:16" else "9:16")
    if other and not heroes_are_distinct(other, metadata):
        raise HTTPException(409, "Duplicate or near-identical hero image across ratios")
    saved = await store_muse_hero(
        tenant_id=tenant, listing_id=listing_id,
        artwork_sha256=row.artwork_sha256, aspect=aspect,
        image_bytes=image_bytes, metadata=metadata,
    )
    db.expire_all()
    locked = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if (locked is None or locked.status != "claimed"
            or locked.artwork_sha256 != row.artwork_sha256
            or not locked.lease_hash or _aware(locked.lease_expires_at) is None
            or _aware(locked.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                locked.lease_hash, hashlib.sha256(lease.encode()).hexdigest()
            )):
        db.rollback()
        raise HTTPException(409, "Muse lease changed during hero upload")
    _assert_muse_video_owner(request, locked)
    updated = dict(locked.output_receipts) if isinstance(locked.output_receipts, dict) else {}
    heroes = dict(updated.get("hero_sources") or {})
    current = heroes.get(ratio)
    current_revisions = (updated.get("hero_revisions") or {}).get(ratio) or []
    if (ratio in (updated.get("drive_uploads") or {})
            or (current or {}).get("sha256") != (previous or {}).get("sha256")
            or (current and (
                not _continuous_gen_enabled()
                or expected_previous_sha != current.get("sha256")
                or len(current_revisions) >= 2))):
        db.rollback()
        raise HTTPException(409, "Hero changed, video stored, or repair limit reached")
    counterpart = heroes.get("16:9" if ratio == "9:16" else "9:16")
    if counterpart and not heroes_are_distinct(counterpart, saved):
        db.rollback()
        raise HTTPException(409, "Hero source matches another aspect")
    superseded_preflight = None
    if current:
        # A preflight cannot outlive the source SHA it attests. Preserve the
        # previous proof in a bounded revision audit and require a new proof.
        proofs = dict(updated.get("story_action_preflight") or {})
        superseded_preflight = proofs.get(ratio)
        if (superseded_preflight is not None and
                superseded_preflight.get("sourceFrameSha256") != current.get("sha256")):
            db.rollback()
            raise HTTPException(409, "Existing preflight changed during repair")
        proofs.pop(ratio, None)
        if proofs:
            updated["story_action_preflight"] = proofs
        else:
            updated.pop("story_action_preflight", None)
        history = dict(updated.get("hero_revisions") or {})
        history[ratio] = [*current_revisions, {
            "previousSource": current,
            "supersededBySha256": saved["sha256"],
            "supersededPreflight": superseded_preflight,
            "reason": "continuous_i2v_soft_gate_hero_correction",
            "supersededAt": _utc().isoformat(),
        }]
        updated["hero_revisions"] = history
    heroes[ratio] = saved
    updated["hero_sources"] = heroes
    updated["heroSourceQa"] = "pending_independent_visual_review"
    locked.output_receipts = updated
    locked.updated_at = _utc()
    db.commit()
    return {"ok": True, "reused": False, "aspectRatio": ratio,
            "imageSha256": saved["sha256"],
            "heroRequestId": saved["heroRequestId"],
            "differentFromOtherAspectVerified": counterpart is not None,
            "storageVerified": True, "faceVisibilityQa": "pending_review",
            "oldHeroPreservedInRevisionHistory": bool(previous),
            "oldPreflightInvalidated": superseded_preflight is not None,
            "heroRevisionCount": len((updated.get("hero_revisions") or {}).get(ratio) or []),
            "nextStep": "Submit a new ratio-specific preflight bound to this image SHA",
            "physicalPlacementCertified": False}


class V4FabricTakeGeometry(BaseModel):
    lowerCollarBottomYpx: float = Field(ge=0, le=4096)
    garmentReferenceWidthPx: float = Field(gt=30, le=4096)
    garmentReferenceWidthMm: float = Field(ge=350, le=750)
    printCenterXpx: float = Field(ge=0, le=4096)
    printWidthMm: float = Field(ge=120, le=320)
    shirtHemYpx: float = Field(ge=0, le=4096)
    calibrationUncertaintyMm: float = Field(ge=0, le=2.5)
    plainC1717ShirtVerified: Literal[True]
    frontFabricPlaneMeasured: Literal[True]
    noExistingPrintOnPlainSourceVerified: Literal[True]
    garmentPolygonPx: list[tuple[float, float]] = Field(min_length=4, max_length=16)


class V4FabricVideoGeometry(BaseModel):
    takeA: V4FabricTakeGeometry
    takeB: V4FabricTakeGeometry
    cutSecond: Literal[6.0] = 6.0
    workerBlankVideoVerified: Literal[True]
    twoGenuineSourceTakesVerified: Literal[True]
    sourceShirtSku: Literal["C1717"]
    sourceNativeAspectVerified: Literal[True]
    calibrationBasis: Literal["measured_on_same_shirt_fabric_plane"]


class V4FabricWorkerProof(BaseModel):
    """Worker-rendered output provenance; DOES NOT establish visual QA PASS."""
    contract: Literal["CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1"]
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plainVideoSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    printedVideoSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    aspectRatio: Literal["vertical", "horizontal"]
    framesProcessed: int = Field(ge=144, le=400)
    fps: float = Field(ge=12, le=30)
    durationSeconds: float = Field(ge=11.7, le=12.3)
    takeCutSecond: Literal[6.0]
    takes: list[dict] = Field(min_length=2, max_length=2)
    tracking: Literal["dense_backward_optical_flow_farneback_half_resolution"]
    artworkWarp: Literal["per_frame_cotton_fabric_backward_remap"]
    manualFabricSurfaceQa: Literal["pending_independent_visual_review"]
    notVerifiedAs3dClothTracking: Literal[True]
    temporalQaNotPassed: Literal[True]


_V4_FABRIC_RENDER_LIMIT = asyncio.Semaphore(1)


def _v4_fabric_pilot_enabled() -> bool:
    """One CAM pilot at a time. Never enable all-job rendering by default."""
    return (
        _story_v33_enabled() and
        os.environ.get("CAM_MUSE_V4_FABRIC_PILOT_ENABLED", "false")
          .strip().lower() == "true"
    )


@router.post(
    "/jobs/{listing_id}/videos/{aspect}/fabric-composite",
    openapi_extra={"requestBody": {"required": True, "content": {
        "video/mp4": {"schema": {"type": "string", "format": "binary"}}
    }}},
)
async def upload_v4_fabric_composite(
    listing_id: str, aspect: Literal["vertical", "horizontal"],
    request: Request,
    x_muse_lease: str | None = Header(default=None),
    x_cam_v4_fabric_contract: str | None = Header(default=None),
    x_cam_v4_fabric_geometry: str | None = Header(default=None),
    x_cam_hero_sha256: str | None = Header(default=None),
    x_cam_hero_request_id: str | None = Header(default=None),
    x_cam_source_frame_sha256: str | None = Header(default=None),
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Pilot-only native-master print -> tracked moving fabric H264 video.

    The incoming 12s movie is PLAIN C1717 (two real 6s takes).
    A bounded CPU compositor bends native pixels along measured cotton flow,
    then CAM verifies+stores ONLY the resulting MP4 on private Drive. Every
    product/print/story/fabric QA state remains pending independent review.
    Existing completed outputs are NEVER overwritten.
    """
    from .fabric_video_compositor import (
        CONTRACT, MAX_SOURCE_MP4_BYTES, composite_blank_c1717_video,
    )
    from .video_storage import store_muse_video
    if (not _v4_fabric_pilot_enabled() or
            listing_id not in _story_v33_pilot_listings()):
        raise HTTPException(409, "V4 cloth tracking restricted to V3.3 canaries")
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    if (request.headers.get("content-type","").split(";")[0].lower() != "video/mp4"
            or x_cam_v4_fabric_contract != CONTRACT
            or not x_cam_v4_fabric_geometry
            or len(x_cam_v4_fabric_geometry) > 5500):
        raise HTTPException(422, "Plain MP4 and bounded V4 fabric contract/geometry required")
    try:
        geometry = V4FabricVideoGeometry.model_validate_json(x_cam_v4_fabric_geometry)
    except ValueError as exc:
        raise HTTPException(422, "Invalid two-take measured C1717 fabric geometry") from exc
    lease = x_muse_lease or ""
    ratio = "9:16" if aspect == "vertical" else "16:9"
    row = db.get(CamMuseJob, (tenant, listing_id))
    if (row is None or row.status != "claimed"
            or not row.artwork_sha256 or not row.artwork_asset_path
            or row.source_state != "ready_for_muse"
            or not row.lease_hash or not 32 <= len(lease) <= 120
            or _aware(row.lease_expires_at) is None
            or _aware(row.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                row.lease_hash, hashlib.sha256(lease.encode()).hexdigest())):
        raise HTTPException(409, "Claimed V3.3 V4 pilot lease required")
    _assert_muse_video_owner(request, row)
    pin = _pinned_story_v33(row)
    receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
    hero = (receipts.get("hero_sources") or {}).get(ratio)
    preflight = (receipts.get("story_action_preflight") or {}).get(ratio)
    if (not pin or _story_lite_pin(pin) or
            ratio in (receipts.get("drive_uploads") or {}) or
            not isinstance(hero, dict) or
            hero.get("storageVerified") is not True or
            hero.get("sha256") != x_cam_hero_sha256 or
            hero.get("heroRequestId") != x_cam_hero_request_id or
            not isinstance(preflight, dict) or
            preflight.get("policyVersion") != PLACEMENT_PROOF_VERSION or
            preflight.get("sourceFrameSha256") != x_cam_source_frame_sha256 or
            preflight.get("sourceFrameSha256") != hero.get("sha256") or
            preflight.get("artworkSha256") != row.artwork_sha256 or
            preflight.get("verificationStatus") !=
                "worker_reported_pending_independent_visual_review" or
            (hero.get("calibratedComposite") or {}).get("policyVersion") !=
                "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"):
        raise HTTPException(409, "V4 requires matching ratio hero, 150mm preflight, and zero saved MP4s")
    if not ARTWORK_PATH.fullmatch(row.artwork_asset_path):
        raise HTTPException(409, "Approved CAM Native Master path required")
    job_id, asset_id = row.artwork_asset_path.split("/")[6:8]
    if _stored_native_asset(db, tenant, listing_id, row.artwork_sha256) != (job_id,asset_id):
        raise HTTPException(409, "Approved master identity mismatch")
    # The first take must have substantially identical source geometry to
    # its CAM hero. The second take is independently measured after 6s cut.
    first = geometry.takeA
    calib = hero["calibratedComposite"]
    if (abs(first.lowerCollarBottomYpx - calib["lowerCollarBottomYpx"]) > 4
            or abs(first.garmentReferenceWidthPx - calib["garmentReferenceWidthPx"]) > 6
            or abs(first.garmentReferenceWidthMm - calib["garmentReferenceWidthMm"]) > .1
            or abs(first.printCenterXpx - calib["printCenterXpx"]) > 8
            or abs(first.printWidthMm - calib["printWidthMm"]) > .1):
        raise HTTPException(409, "V4 take A geometry does not match measured hero source")
    incoming = bytearray()
    async for chunk in request.stream():
        incoming.extend(chunk)
        if len(incoming) > MAX_SOURCE_MP4_BYTES:
            raise HTTPException(413, "Blank video exceeds V4 pilot 40 MiB limit")
    if len(incoming) < 2048:
        raise HTTPException(422, "Missing blank C1717 two-take MP4")
    # Read only the approved original master using the existing Drive OAuth.
    remote = _private_drive_design(job_id,asset_id,tenant,db)
    if remote:
        if remote.get("output_sha256") != row.artwork_sha256:
            raise HTTPException(409, "Drive Native Master SHA changed")
        db.rollback()
        from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
        master = await read_design_from_drive(
            tenant_id=tenant,asset_id=job_id+":"+asset_id+":original",
            remote_id=str(remote.get("drive_file_id") or ""),
            sha256=row.artwork_sha256,max_bytes=20*1024*1024,
        )
    else:
        local, sha = _checked_design_media(job_id,asset_id,db,tenant)
        if sha != row.artwork_sha256:
            raise HTTPException(409,"Native Master digest mismatch")
        db.rollback()
        master = await run_in_threadpool(local.read_bytes)
    if hashlib.sha256(master).hexdigest()!=row.artwork_sha256:
        raise HTTPException(409,"Native Master bytes SHA mismatch")
    async with _V4_FABRIC_RENDER_LIMIT:
        printed, evidence = await asyncio.to_thread(
            composite_blank_c1717_video,
            plain_mp4=bytes(incoming),artwork_png=master,aspect=aspect,
            take_a=geometry.takeA.model_dump(),
            take_b=geometry.takeB.model_dump(),cut_second=6.0,
        )
    # Only the finished video is stored; plain input is never persisted.
    stored = await store_muse_video(
        tenant_id=tenant, listing_id=listing_id,
        artwork_sha256=row.artwork_sha256,
        aspect=aspect, mp4=printed,
        required_duration_seconds=STORY_FINAL_DURATION_BOUNDS,
    )
    stored.update({
        "motionPromptVersion": STORY_VERSION,
        "motionVersionSource": "cam_v4_deterministic_compositor_executed",
        "storyVersion": STORY_VERSION,
        "storyOutputContract": STORY_OUTPUT_CONTRACT,
        "storySourceContract": STORY_SOURCE_CONTRACT,
        "heroImageSha256": hero["sha256"],
        "heroImageRequestId": hero["heroRequestId"],
        "heroSourceVerified": True,
        "garmentSkuContract": GARMENT_SKU,
        "garmentPolicyVersion": GARMENT_POLICY_VERSION,
        "sourcePlacementProofVersion": PLACEMENT_PROOF_VERSION,
        "sourceFrameSha256": preflight["sourceFrameSha256"],
        "sourceWorkerMeasuredGapMm": preflight["workerCalculatedGapMm"],
        "sourceEstimatedPlacementErrorMm": preflight.get(
            "maximumEstimatedPlacementErrorMm"),
        "sourcePlacementQa": "worker_reported_pending_independent_visual_review",
        "storyQaStatus": "pending_review",
        "fabricSurfaceQaStatus": "pending_independent_visual_review",
        "fabricCompositeContract": CONTRACT,
        "fabricCompositeProof": evidence,
    })
    db.expire_all()
    locked = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if (locked is None or locked.status != "claimed"
            or locked.artwork_sha256 != row.artwork_sha256
            or not locked.lease_hash
            or _aware(locked.lease_expires_at) is None
            or _aware(locked.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                locked.lease_hash,hashlib.sha256(lease.encode()).hexdigest())):
        db.rollback()
        raise HTTPException(409,"V4 lease changed during composite/Drive storage")
    _assert_muse_video_owner(request,locked)
    latest=dict(locked.output_receipts) if isinstance(locked.output_receipts,dict) else {}
    active_hero=(latest.get("hero_sources") or {}).get(ratio)
    if (not isinstance(active_hero,dict) or
            active_hero.get("sha256") != hero["sha256"] or
            ratio in (latest.get("drive_uploads") or {})):
        db.rollback()
        raise HTTPException(409,"V4 source changed or video already stored")
    uploads=dict(latest.get("drive_uploads") or {})
    uploads[ratio]=stored
    latest["drive_uploads"]=uploads
    if set(uploads)=={"9:16","16:9"}:
        latest["videos"]=[uploads["9:16"],uploads["16:9"]]
        latest["camLinked"]=False
        latest["videoContentQa"]="pending_review"
        latest["videoGarmentQa"]="pending_independent_visual_review"
        latest["videoQaApproval"]="pending_independent_review"
        latest["videoStoryQa"]="pending_independent_visual_review"
        latest["videoPrintQa"]="pending_independent_visual_review"
        latest["videoFabricSurfaceQa"]="pending_independent_visual_review"
        latest["video150mmPlacementQa"]="worker_reported_pending_independent_visual_review"
        locked.status="submitted"
        locked.submitted_at=_utc()
        locked.lease_hash=None
        locked.lease_expires_at=None
    locked.output_receipts=latest
    locked.updated_at=_utc()
    db.commit()
    return {
        "ok":True,"status":locked.status,
        "aspectRatio":ratio,"storedCount":len(uploads),
        "printedVideoSha256":stored["sha256"],
        "blankVideoSha256":evidence["plainVideoSha256"],
        "fabricCompositeContract":CONTRACT,
        "framesTracked":evidence["framesProcessed"],
        "independentFabricQa":"pending_review",
        "camLinked":False,
        "sourceTemporaryWorkingFilesDeletedAfterEncode":True,
    }


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
        "CAM_STORY_REAL_LIFE_V3_3",
        "CAM_C1717_12S_REAL_ACTION_V1",
    ] | None = Header(default=None),
    x_cam_story_action_contract: Literal["CAM_C1717_12S_REAL_ACTION_V1"] | None = Header(default=None),
    x_cam_placement_proof_contract: Literal["CAM_C1717_150MM_SOURCE_MEASUREMENT_V1"] | None = Header(default=None),
    x_cam_source_frame_sha256: str | None = Header(default=None),
    x_cam_story_output_contract: Literal["CAM_STORY_12S_TWO_TAKE_V1", "CAM_STORY_NATIVE_FLEX_V1"] | None = Header(default=None),
    x_cam_story_source_contract: Literal["CAM_STORY_DUAL_HERO_FACE_V1", "CAM_STORY_CHARACTER_SOURCE_V1"] | None = Header(default=None),
    x_cam_hero_sha256: str | None = Header(default=None),
    x_cam_hero_request_id: str | None = Header(default=None),
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
    _assert_muse_video_owner(request, row)
    story_pin = _pinned_story_v33(row)
    lite = _story_lite_pin(story_pin)
    strict_story = bool(story_pin and not lite)
    action_pin = _pinned_action_story_v1(row)
    if (strict_story and _v4_fabric_pilot_enabled()
            and listing_id in _story_v33_pilot_listings()):
        raise HTTPException(
            409,
            "V4 pilot requires real tracked plain C1717 MP4 through "
            "/videos/{aspect}/fabric-composite; direct printed MP4 blocked",
        )
    ratio = "9:16" if aspect == "vertical" else "16:9"
    if ratio not in _job_required_aspects(row):
        raise HTTPException(409, "Aspect deferred by this job claim; finish required aspect only")
    action_preflight = None
    if action_pin or strict_story:
        if ((action_pin and x_cam_motion_version != STORY_ACTION_VERSION)
                or x_cam_story_action_contract != STORY_ACTION_VERSION
                or x_cam_placement_proof_contract != PLACEMENT_PROOF_VERSION):
            raise HTTPException(422, "Story-action and 150mm placement proof headers required")
        if not x_cam_source_frame_sha256 or not HEX64.fullmatch(x_cam_source_frame_sha256):
            raise HTTPException(422, "Approved C1717 source frame SHA required")
        receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
        action_preflight = (receipts.get("story_action_preflight") or {}).get(ratio)
        if (not isinstance(action_preflight, dict)
                or action_preflight.get("policyVersion") != PLACEMENT_PROOF_VERSION
                or action_preflight.get("sourceFrameSha256") != x_cam_source_frame_sha256
                or action_preflight.get("artworkSha256") != row.artwork_sha256
                or action_preflight.get("verificationStatus") !=
                    "worker_reported_pending_independent_visual_review"):
            raise HTTPException(409, "Missing ratio-specific calibrated C1717 hero preflight")
    if story_pin and x_cam_motion_version != STORY_VERSION:
        raise HTTPException(422, "Story V3.3 upload requires matching motion-version header")
    if story_pin and x_cam_story_output_contract != story_pin["storyOutputContract"]:
        raise HTTPException(422, "Use the story output contract pinned for this listing")
    if strict_story and x_cam_story_source_contract != STORY_SOURCE_CONTRACT:
        raise HTTPException(422, "Strict V3.3 requires independent face-visible hero contract")
    if lite and isinstance(x_cam_story_source_contract, str) and x_cam_story_source_contract != LITE_SOURCE:
        raise HTTPException(422, "Use Story Lite source contract or omit the optional header")
    if ((x_cam_motion_version == STORY_VERSION
            or x_cam_story_output_contract is not None
            or x_cam_story_source_contract is not None) and not story_pin):
        raise HTTPException(409, "Story V3.3 requires a pinned capable-worker claim")
    ratio = "9:16" if aspect == "vertical" else "16:9"
    verified_hero = None
    if strict_story:
        receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
        verified_hero = (receipts.get("hero_sources") or {}).get(ratio)
        expected_plan = _story_candidate_v33(row)
        if (not isinstance(verified_hero, dict) or not verified_hero.get("storageVerified")
                or expected_plan is None
                or verified_hero.get("heroRequestId") !=
                    expected_plan["aspectPlans"][ratio]["heroRequestId"]
                or x_cam_hero_request_id != verified_hero.get("heroRequestId")
                or x_cam_hero_sha256 != verified_hero.get("sha256")):
            raise HTTPException(409, "V3.3 requires the uploaded original hero image for this ratio")
    parts = bytearray()
    async for chunk in request.stream():
        parts.extend(chunk)
        if len(parts) > MAX_VIDEO_BYTES:
            raise HTTPException(413, "MP4 exceeds 40 MiB limit")
    if len(parts) < 2048:
        raise HTTPException(422, "Missing MP4")
    digest = hashlib.sha256(parts).hexdigest()
    existing_receipts = row.output_receipts if isinstance(row.output_receipts, dict) else {}
    removed = (existing_receipts.get("videoRegeneration") or {}).get("removedSha256ByAspect") or {}
    if removed.get(ratio) == digest:
        raise HTTPException(409, "Deleted MP4 cannot be re-uploaded; generate fresh video")
    previous = (existing_receipts.get("drive_uploads") or {}).get(ratio)
    if isinstance(previous, dict):
        if previous.get("sha256") != digest:
            raise HTTPException(409, "An alternate video for this aspect was already saved")
        return {"ok": True, "reused": True, "status": row.status,
                "aspectRatio": ratio, "storageVerified": previous.get("storageVerified") is True,
                "camLinked": bool(existing_receipts.get("camLinked")),
                "videoContentQa": existing_receipts.get("videoContentQa", "pending_review"),
                "motionPromptVersion": previous.get("motionPromptVersion"),
                "driveFolder": previous.get("relativeFolder")}
    expected_artwork_sha = row.artwork_sha256
    storage_kwargs = {
        "tenant_id": tenant, "listing_id": listing_id,
        "artwork_sha256": expected_artwork_sha,
        "aspect": aspect, "mp4": bytes(parts),
    }
    if lite:
        storage_kwargs["required_duration_seconds"] = LITE_DURATION_BOUNDS
    elif strict_story or action_pin:
        storage_kwargs["required_duration_seconds"] = STORY_FINAL_DURATION_BOUNDS
    stored = await store_muse_video(**storage_kwargs)
    if action_pin:
        stored["storyActionVersion"] = STORY_ACTION_VERSION
        stored["sourcePlacementProofVersion"] = PLACEMENT_PROOF_VERSION
        stored["sourceFrameSha256"] = action_preflight["sourceFrameSha256"]
        stored["sourceWorkerMeasuredGapMm"] = action_preflight["workerCalculatedGapMm"]
        stored["storyActionQa"] = "pending_independent_visual_review"
        stored["sourcePlacementQa"] = "worker_reported_pending_independent_visual_review"
    if lite:
        stored.update({
            "storyVersion": STORY_VERSION,
            "storyOutputContract": LITE_OUTPUT,
            "storySourceContract": LITE_SOURCE,
            "creativePolicy": LITE_POLICY,
            "storyQaStatus": "pending_review",
            "characterQaStatus": "pending_review",
            "sourcePlacementQa": "not_required",
            "sourceVisualContentIndependentlyVerified": False,
        })
    if strict_story:
        stored["sourcePlacementProofVersion"] = PLACEMENT_PROOF_VERSION
        stored["sourceFrameSha256"] = action_preflight["sourceFrameSha256"]
        stored["sourceWorkerMeasuredGapMm"] = action_preflight["workerCalculatedGapMm"]
        stored["sourceEstimatedPlacementErrorMm"] = (
            action_preflight.get("maximumEstimatedPlacementErrorMm")
        )
        stored["sourcePlacementQa"] = "worker_reported_pending_independent_visual_review"
        stored["storyVersion"] = STORY_VERSION
        stored["storyOutputContract"] = STORY_OUTPUT_CONTRACT
        stored["storySourceContract"] = STORY_SOURCE_CONTRACT
        stored["heroImageSha256"] = verified_hero["sha256"]
        stored["heroImageRequestId"] = verified_hero["heroRequestId"]
        stored["heroSourceVerified"] = True
        stored["faceVisibilityQa"] = "pending_review"
        stored["storyQaStatus"] = "pending_review"
    # Encoder metadata cannot establish whether the visible garment is a
    # C1717 tee. Never turn upload success into an automatic garment QA PASS.
    stored["garmentSkuContract"] = GARMENT_SKU
    stored["garmentPolicyVersion"] = GARMENT_POLICY_VERSION
    stored["garmentQaStatus"] = "pending_independent_visual_review"
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
    _assert_muse_video_owner(request, locked)
    receipts = dict(locked.output_receipts) if isinstance(locked.output_receipts, dict) else {}
    uploads = dict(receipts.get("drive_uploads") or {})
    if lite and not _story_lite_pin(_pinned_story_v33(locked)):
        db.rollback()
        raise HTTPException(409, "Story Lite contract changed during MP4 upload")
    if strict_story:
        current_hero = (receipts.get("hero_sources") or {}).get(ratio)
        if (not isinstance(current_hero, dict)
                or current_hero.get("sha256") != verified_hero["sha256"]
                or current_hero.get("heroRequestId") != verified_hero["heroRequestId"]):
            db.rollback()
            raise HTTPException(409, "Hero source changed during MP4 upload")
    if ratio in uploads and uploads[ratio].get("sha256") != digest:
        db.rollback()
        raise HTTPException(409, "Conflicting video variant")
    uploads[ratio] = stored
    receipts["drive_uploads"] = uploads
    # Storage and visual QA are separate, including the first aspect.
    receipts["videoContentQa"] = "pending_review"
    receipts["videoGarmentQa"] = "pending_independent_visual_review"
    receipts["camLinked"] = False
    receipts["videoLastStoredAtUtc"] = _utc().isoformat()
    if lite:
        receipts["videoStoryQa"] = "pending_review"
        receipts["videoCharacterQa"] = "pending_review"
        receipts["video150mmPlacementQa"] = "not_required"
    if set(uploads) == {"9:16", "16:9"}:
        # Storage verified != video content approved. Listing UI can preview it,
        # but must flag both variants as pending video content review.
        receipts["videos"] = [uploads["9:16"], uploads["16:9"]]
        # Drive receipt means saved, NOT independently approved. Legacy
        # already-completed listings retain their historical linkage; a V3.3
        # story requires the separate operator video QA before CAM linkage.
        receipts["camLinked"] = not strict_story
        receipts["videoContentQa"] = "pending_review"
        receipts["videoGarmentQa"] = "pending_independent_visual_review"
        if lite:
            receipts["videoQaApproval"] = "not_required_for_linkage"
            receipts["videoStoryQa"] = "pending_review"
            receipts["videoCharacterQa"] = "pending_review"
            receipts["videoPrintQa"] = "pending_review"
            receipts["video150mmPlacementQa"] = "not_required"
        if strict_story:
            receipts["videoQaApproval"] = "pending_independent_review"
            receipts["videoStoryQa"] = "pending_independent_visual_review"
            receipts["videoPrintQa"] = "pending_independent_visual_review"
            receipts["videoFabricSurfaceQa"] = "pending_independent_visual_review"
        if action_pin:
            receipts["videoStoryActionQa"] = "pending_independent_visual_review"
        if action_pin or strict_story:
            receipts["video150mmPlacementQa"] = "worker_reported_pending_independent_visual_review"
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
            "videoGarmentQa": "pending_independent_visual_review",
            "storyActionQa": "pending_independent_visual_review" if action_pin else None,
            "placementQa": ("not_required" if lite else
                "worker_reported_pending_independent_visual_review" if (action_pin or strict_story) else None),
            "creativePolicy": LITE_POLICY if lite else None,
            "garmentSkuContract": GARMENT_SKU,
            "garmentPolicyVersion": GARMENT_POLICY_VERSION,
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


class FabricSurfaceQaEvidence(BaseModel):
    """Operator-observed print attachment to a moving shirt (not codec metadata).

    The exact Native Master characters/colors remain unchanged, while its
    apparent geometry MUST deform with the real garment's surface.
    """
    printFollowsTorsoCurvature: bool
    printMovesWithFabric: bool
    printFollowsVisibleFolds: bool
    printShadingMatchesFabric: bool
    printOcclusionMatchesFabric: bool
    noFloatingStickerEdges: bool
    printPerspectiveChangesWithBody: bool
    observedFrameSeconds: list[float] = Field(min_length=3, max_length=12)
    observations: str = Field(min_length=35, max_length=600)


class IndependentVideoQaVerdict(BaseModel):
    """Independent CAM operator review of already-saved V3.3 MP4s.

    Attestations never substitute for watching the actual Drive videos.
    Muse workers do not possess the operator credential for this endpoint.
    """
    verdict: Literal["pass", "hold"]
    reviewer: str = Field(min_length=6, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    reviewedVideoSha256ByAspect: dict[str, str]
    reviewedHeroSha256ByAspect: dict[str, str]
    sampledFrameSecondsByAspect: dict[str, list[float]]
    fabricSurfaceQaByAspect: dict[str, FabricSurfaceQaEvidence]
    faceVisibleSecondsByAspect: dict[str, float]
    genuineMotionSecondsByAspect: dict[str, float]
    twoTakeCutSecondByAspect: dict[str, float]
    storyThreeActsVerified: bool
    twoGenuineTakesVerified: bool
    noLoopOrFrozenFramesVerified: bool
    c1717ShortSleeveVerified: bool
    nativeMasterPrintUnchangedVerified: bool
    print150mmAcrossMotionVerified: bool
    independentHeroesAndFramingVerified: bool
    notes: str = Field(min_length=80, max_length=1800)


@router.post("/jobs/{listing_id}/video-qa", include_in_schema=False)
def approve_v33_video_content(
    listing_id: str, payload: IndependentVideoQaVerdict,
    tenant: str = Depends(_operator), db: Session = Depends(get_db),
):
    """Gate V3.3 CAM linkage on an independent operator's video inspection.

    This endpoint verifies hashes, Drive receipts, source preflights and
    bounded sampling metadata. Story/motion/print facts remain an explicit
    operator attestation; never claim they were inferred from MP4 metadata.
    """
    if not LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    row = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if row is None or row.status != "submitted" or not _pinned_story_v33(row):
        raise HTTPException(409, "Only uploaded V3.3 stories accept independent video review")
    if _story_lite_pin(_pinned_story_v33(row)):
        raise HTTPException(409, "Story Lite uses nonblocking content review, not strict geometry/take QA")
    receipts = dict(row.output_receipts) if isinstance(row.output_receipts, dict) else {}
    existing = receipts.get("independentVideoReviewV33")
    if existing is not None:
        if (isinstance(existing, dict) and existing.get("verdict") == payload.verdict
                and existing.get("reviewedVideoSha256ByAspect") ==
                    payload.reviewedVideoSha256ByAspect):
            return {"ok": True, "reused": True, "verdict": existing["verdict"],
                    "camLinked": bool(receipts.get("camLinked"))}
        raise HTTPException(409, "Independent video review is immutable for these stored MP4s")
    uploads = receipts.get("drive_uploads") or {}
    heroes = receipts.get("hero_sources") or {}
    preflights = receipts.get("story_action_preflight") or {}
    required_ratios = {"9:16", "16:9"}
    evidence_maps = (
        payload.reviewedVideoSha256ByAspect,
        payload.reviewedHeroSha256ByAspect,
        payload.sampledFrameSecondsByAspect,
        payload.fabricSurfaceQaByAspect,
        payload.faceVisibleSecondsByAspect,
        payload.genuineMotionSecondsByAspect,
        payload.twoTakeCutSecondByAspect,
    )
    if (any(set(m) != required_ratios for m in (uploads, heroes, preflights, *evidence_maps))
            or len(receipts.get("videos") or []) != 2):
        raise HTTPException(409, "Both ratio MP4s, heroes, and placement preflights are required")
    for ratio in ("9:16", "16:9"):
        clip, hero, preflight = uploads[ratio], heroes[ratio], preflights[ratio]
        if not all(isinstance(item, dict) for item in (clip, hero, preflight)):
            raise HTTPException(409, "Malformed story source or video receipts")
        duration = clip.get("durationSeconds")
        measured_mm = preflight.get("workerCalculatedGapMm")
        if (clip.get("storageVerified") is not True or
                hero.get("storageVerified") is not True or
                clip.get("storyVersion") != STORY_VERSION or
                clip.get("storyOutputContract") != STORY_OUTPUT_CONTRACT or
                clip.get("garmentSkuContract") != GARMENT_SKU or
                clip.get("sourcePlacementProofVersion") != PLACEMENT_PROOF_VERSION or
                preflight.get("policyVersion") != PLACEMENT_PROOF_VERSION or
                clip.get("sha256") != payload.reviewedVideoSha256ByAspect[ratio] or
                hero.get("sha256") != payload.reviewedHeroSha256ByAspect[ratio] or
                preflight.get("sourceFrameSha256") != hero.get("sha256") or
                clip.get("sourceFrameSha256") != hero.get("sha256") or
                clip.get("heroImageSha256") != hero.get("sha256") or
                clip.get("sourceWorkerMeasuredGapMm") != measured_mm or
                (_deterministic_hero_required() and (
                    not isinstance(preflight.get("maximumEstimatedPlacementErrorMm"),
                                   (float, int))
                    or preflight["maximumEstimatedPlacementErrorMm"] > PLACEMENT_TOLERANCE_MM
                    or clip.get("sourceEstimatedPlacementErrorMm") !=
                        preflight["maximumEstimatedPlacementErrorMm"]
                    or (hero.get("calibratedComposite") or {}).get("policyVersion") !=
                        "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"
                )) or
                not isinstance(measured_mm, (float, int)) or
                abs(measured_mm - PLACEMENT_TARGET_MM) > _placement_tolerance_mm() or
                not isinstance(duration, (float, int)) or
                not STORY_FINAL_DURATION_BOUNDS[0] <= duration <= STORY_FINAL_DURATION_BOUNDS[1]):
            raise HTTPException(409, "Independent QA did not match stored video, hero or 150mm receipt")
        samples = payload.sampledFrameSecondsByAspect[ratio]
        if (not 5 <= len(samples) <= 20
                or any(type(t) not in (float, int) or not 0 <= t <= duration for t in samples)
                or any(next_t <= t for t, next_t in zip(samples, samples[1:]))
                or samples[0] > 0.5 or samples[-1] < 11.5
                or not any(2.5 <= t <= 3.5 for t in samples)
                or not any(5.75 <= t <= 6.25 for t in samples)
                or not any(8.5 <= t <= 9.5 for t in samples)):
            raise HTTPException(422, "QA samples must cover the complete two-take three-act story")
        if (not 0 <= payload.faceVisibleSecondsByAspect[ratio] <= duration
                or not 0 <= payload.genuineMotionSecondsByAspect[ratio] <= duration
                or not 5.5 <= payload.twoTakeCutSecondByAspect[ratio] <= 6.5):
            raise HTTPException(422, "QA motion timings do not fit the uploaded clip")
        surface = payload.fabricSurfaceQaByAspect[ratio]
        observed = surface.observedFrameSeconds
        if (any(type(t) not in (int, float) or not 0 <= t <= duration for t in observed)
                or any(b <= a for a, b in zip(observed, observed[1:]))
                or observed[0] > 1.0 or observed[-1] < 9.0
                or not any(3.0 <= t <= 9.0 for t in observed)):
            raise HTTPException(
                422, "Fabric surface inspection must cover start, moving action and ending"
            )
        if payload.verdict == "pass" and not all((
                surface.printFollowsTorsoCurvature,
                surface.printMovesWithFabric,
                surface.printFollowsVisibleFolds,
                surface.printShadingMatchesFabric,
                surface.printOcclusionMatchesFabric,
                surface.noFloatingStickerEdges,
                surface.printPerspectiveChangesWithBody,
        )):
            raise HTTPException(
                422, "V3.3 cannot pass: floating sticker/planar print does not follow fabric"
            )
    if payload.verdict == "pass":
        checks = (
            payload.storyThreeActsVerified, payload.twoGenuineTakesVerified,
            payload.noLoopOrFrozenFramesVerified, payload.c1717ShortSleeveVerified,
            payload.nativeMasterPrintUnchangedVerified,
            payload.print150mmAcrossMotionVerified,
            payload.independentHeroesAndFramingVerified,
        )
        if (not all(value is True for value in checks)
                or any(payload.faceVisibleSecondsByAspect[r] < 8 for r in required_ratios)
                or any(payload.genuineMotionSecondsByAspect[r] < 6 for r in required_ratios)):
            raise HTTPException(422, "V3.3 story, face, garment, print and motion QA must all pass")
    review = {
        **payload.model_dump(),
        "policyVersion": "CAM_V33_INDEPENDENT_VIDEO_QA_V2_FABRIC_SURFACE",
        "reviewMode": "operator_attested_after_reviewing_stored_drive_mp4",
        "reviewedAt": _utc().isoformat(),
        "artworkSha256": row.artwork_sha256,
    }
    approved = payload.verdict == "pass"
    receipts["independentVideoReviewV33"] = review
    receipts["videoQaApproval"] = "approved" if approved else "hold"
    receipts["videoContentQa"] = "pass" if approved else "hold"
    receipts["videoStoryQa"] = "pass" if approved else "hold"
    receipts["videoPrintQa"] = "pass" if approved else "hold"
    receipts["videoFabricSurfaceQa"] = "pass" if approved else "hold"
    receipts["videoGarmentQa"] = "pass" if approved else "hold"
    receipts["video150mmPlacementQa"] = "pass" if approved else "hold"
    receipts["camLinked"] = approved
    if approved:
        updated = dict(uploads)
        for ratio, clip in updated.items():
            updated[ratio] = {
                **clip, "storyQaStatus": "pass", "garmentQaStatus": "pass",
                "sourcePlacementQa": "pass", "printQaStatus": "pass",
                "fabricSurfaceQaStatus": "pass",
            }
        receipts["drive_uploads"] = updated
        receipts["videos"] = [updated["9:16"], updated["16:9"]]
    row.output_receipts = receipts
    row.updated_at = _utc()
    db.commit()
    return {
        "ok": True, "reused": False, "verdict": payload.verdict,
        "camLinked": approved, "reviewPolicyVersion": review["policyVersion"],
    }


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


class WorkerYieldRequest(BaseModel):
    lease: str = Field(min_length=32, max_length=128)
    reason: Literal["provider_rate_limit", "account_cooldown"]
    retryAfterSeconds: int = Field(ge=60, le=86400)


class WorkerCooldownReport(BaseModel):
    providerStatus: Literal[429]
    reason: Literal["provider_rate_limit", "video_daily_budget_exhausted"] = "provider_rate_limit"
    retryAfterSeconds: int = Field(ge=60, le=86400)


class OperatorSoftHoldRequest(BaseModel):
    listingId: str = Field(pattern=r"^[0-9]{5,20}$")
    holdSeconds: int = Field(ge=1800, le=43200)
    reason: Literal[
        "reported_renderer_block",
        "reported_source_hold",
        "reported_qa_hold",
    ]
    note: str = Field(min_length=12, max_length=240)


class CommandHeartbeat(BaseModel):
    lease: str = Field(min_length=32, max_length=128)
    state: Literal["started", "researching", "collecting", "validating", "uploading", "blocked"]
    note: str | None = Field(default=None, max_length=180)


class CommandAck(BaseModel):
    lease: str = Field(min_length=32, max_length=128)
    outcome: Literal["success", "failed"]
    notes: str = Field(min_length=10, max_length=800)
    result: TaskResult | None = None
    rendererHttpStatus: Literal[502, 503, 504] | None = None
    rendererErrorStage: Literal["generate_video"] | None = None


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
        "camLinked": (isinstance(cmd.details, dict)
                      and cmd.details.get("videoReceiptNotCamLinked") is False),
        "renderRetry": ((cmd.details or {}).get("renderRetry")
                        if isinstance(cmd.details, dict) else None),
    }


# Transient renderer outages are separate from a hard QA/capability HOLD.
_RENDERER_TRANSIENT_HTTP_STATUSES = (502, 503, 504)
_RENDERER_HARD_BLOCKERS = (
    "RENDERER_CAPABILITY_BLOCKED",
    "FABRIC_SURFACE_TRACKING_BLOCKED",
    "PRINT_SURFACE_QA_HOLD",
)


def _reported_renderer_transient_status(notes: str) -> int | None:
    """Conservatively classify worker-reported upstream failures, not CAM errors."""
    note = notes.lower()
    if any(code.lower() in note for code in _RENDERER_HARD_BLOCKERS):
        return None
    if not re.search(r"\brenderer\b|media\.generate_video", note):
        return None
    match = re.search(r"\bhttp\s+(502|503|504)\b", note)
    return int(match.group(1)) if match else None


def _render_retry_wait_seconds(receipts: dict | None, now=None) -> int:
    """Time-limited retry gate shared by listing discovery and actual claim."""
    if not isinstance(receipts, dict):
        return 0
    marker = receipts.get("automationRenderRetry")
    if not isinstance(marker, dict) or not isinstance(marker.get("notBeforeUtc"), str):
        return 0
    try:
        delta = _aware(datetime.fromisoformat(
            marker["notBeforeUtc"].replace("Z", "+00:00")
        )) - (now or _utc())
        return max(0, int(delta.total_seconds() + 0.999999))
    except (TypeError, ValueError):
        return 0


def _schedule_transient_render_retry(receipts: dict, cmd, job, status: int, now) -> dict | None:
    """A retry reuses the same command and never resets either attempt budget."""
    if (status not in _RENDERER_TRANSIENT_HTTP_STATUSES
            or not _continuous_gen_enabled() or cmd.attempts >= 5
            or job.attempts >= 5 or receipts.get("videoCapabilityHold")):
        return None
    previous = receipts.get("automationRenderRetry") or {}
    count = previous.get("retryCount", 0) if isinstance(previous, dict) else 0
    count = count if type(count) is int and 0 <= count <= 5 else 0
    if count >= 3:
        return None
    count += 1
    delay = (180, 360, 720)[count - 1]
    marker = {
        "policyVersion": "CAM_RENDERER_TRANSIENT_RETRY_V1",
        "providerStatusReported": status,
        "providerStatusIndependentlyVerified": False,
        "retryCount": count,
        "retryDelaySeconds": delay,
        "notBeforeUtc": (now + timedelta(seconds=delay)).isoformat(),
        "reportedAtUtc": now.isoformat(),
        "commandId": cmd.command_id,
        "reuseVerifiedHeroesAndPreflight": True,
        "neverRegenerateSavedVideo": True,
    }
    receipts["automationRenderRetry"] = marker
    return marker


def _automation_soft_hold_active(receipts: dict | None) -> bool:
    """Skip a known-bad listing temporarily; keep Drive outputs untouched."""
    if not isinstance(receipts, dict):
        return False
    hold = receipts.get("automationSoftHold")
    if not isinstance(hold, dict):
        return False
    until = hold.get("untilUtc")
    if not isinstance(until, str):
        return False
    try:
        return _aware(datetime.fromisoformat(until.replace("Z", "+00:00"))) > _utc()
    except ValueError:
        # A malformed advisory marker must not indefinitely deadlock a job.
        return False


def _eligible_phase(db: Session, tenant: str,
                    job: CamMuseJob, cmd: CamMuseCommand):
    if job.artwork_sha256 != cmd.artwork_sha256:
        return None
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    if _automation_soft_hold_active(receipts):
        return None
    # Each SHA-matched candidate may now be independently reviewed by Muse.
    # The verdict endpoint still demands real comparative visual evidence.
    if cmd.action == "review_artwork":
        if job.status != "qa_hold" or _qa_record(job) is not None:
            return None
        return ("review_artwork" if job.product_image_url and _review_native_asset(
            db, tenant, job
        ) else None)
    if job.status == "qa_hold":
        if _qa_record(job) is not None:
            return None
        return ("review_artwork" if _review_native_asset(
            db, tenant, job
        ) and job.product_image_url else None)
    if not (set(_job_required_aspects(job)) - _verified_video_ratios(job)):
        return None
    # V3.3 is enabled for the two canaries, not for the whole queue until
    # a real renderer + independent story/print QA pass is verified.
    # In-flight or partially uploaded legacy jobs keep their existing path.
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    if (receipts.get("videoCapabilityHold")
            or _automation_soft_hold_active(receipts)
            or _render_retry_wait_seconds(receipts) > 0):
        return None
    if (not _continuous_gen_enabled()
            and _story_v33_enabled() and not _story_v33_bulk_enabled()
            and job.listing_key not in _story_v33_pilot_listings()
            and not _pinned_story_v33(job) and not _pinned_action_story_v1(job)
            and not receipts.get("drive_uploads") and not receipts.get("videos")):
        return None
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
        qa = _qa_record(job)
        if qa.get("placement_review") is not None:
            match = _stored_native_asset(db, tenant, job.listing_key, job.artwork_sha256)
            if not match or job.artwork_asset_path != (
                f"/api/v1/etsy-spy/image-agents/designs/{match[0]}/{match[1]}/original"
            ):
                return None
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
    effective_request_id = payload.requestId
    resume_after_soft_hold = None
    if prior:
        if prior.listing_key != payload.listingId or prior.action != payload.action:
            raise HTTPException(409, "requestId reused for a different task")
        marker = (
            (job.output_receipts or {}).get("automationSoftHold")
            if isinstance(job.output_receipts, dict) else None
        )
        # The dispatcher uses a stable requestId to avoid duplicate commands.
        # After an explicit operator soft-HOLD, returning its terminal FAILED
        # receipt forever would strand the job. Use a deterministic *new*
        # requestId keyed to the latest hold, preserving the old audit record.
        if (prior.status == "failed"
                and isinstance(prior.details, dict)
                and isinstance(prior.details.get("softHold"), dict)
                and isinstance(marker, dict)
                and isinstance(marker.get("heldCommandId"), str)
                and isinstance(marker.get("untilUtc"), str)):
            if _automation_soft_hold_active(job.output_receipts):
                raise HTTPException(409, "Listing is on temporary operator HOLD")
            suffix = hashlib.sha256((
                marker["heldCommandId"] + ":" + marker["untilUtc"]
            ).encode()).hexdigest()[:12]
            effective_request_id = (
                payload.requestId[:108] + "-resume-" + suffix
            )
            resume_after_soft_hold = marker["heldCommandId"]
            resumed = db.scalar(select(CamMuseCommand).where(
                CamMuseCommand.tenant_id == tenant,
                CamMuseCommand.request_id == effective_request_id,
            ).limit(1))
            if resumed:
                if (resumed.listing_key != payload.listingId
                        or resumed.action != payload.action):
                    raise HTTPException(409, "Soft-HOLD retry identity conflict")
                return {"ok": True, "reused": True, **_command_status(resumed)}
        else:
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
        request_id=effective_request_id,
        artwork_sha256=job.artwork_sha256,
        status="pending",
        phase="review_artwork",
        details={
            "origin": "codelocal-operator",
            "note": payload.note or "",
            "rights_verified": False,
            "autoResumeAfterSoftHoldFrom": resume_after_soft_hold,
        },
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


def _hero_resume_priority(item: dict) -> int:
    """Rank only CAM-verified sources; never grant an artwork/hero QA verdict."""
    claim = item.get("videoJobClaim") or {}
    missing = claim.get("missingVideoAspectRatios") or []
    manifest = claim.get("resumeByAspect") or {}
    scores = []
    for ratio in missing:
        hero = manifest.get(ratio) or {}
        verified = (hero.get("heroStorageVerified") is True
                    and isinstance(hero.get("heroSha256"), str)
                    and HEX64.fullmatch(hero["heroSha256"]))
        if verified and hero.get("reuseHero") is True and hero.get("preflightMatchesHeroAndArtwork") is True:
            scores.append(0)
        elif verified and hero.get("optionalExistingHero") is True:
            scores.append(1)
        else:
            scores.append(2)
    return max(scores, default=2)


def _limit_half_open_video_candidates(items: list[dict], circuit: dict) -> list[dict]:
    """One probe candidate per discovery while preserving review work."""
    if not circuit.get("probeRequired"):
        return items
    seen_video = False
    result = []
    for item in items:
        phase = item.get("phase") or item.get("nextStep")
        if phase == "create_video":
            if seen_video:
                continue
            seen_video = True
        result.append(item)
    return result


def _fair_muse_discovery_order(items: list[dict], tenant: str, db: Session) -> list[dict]:
    """Partial media first, then reusable hero, with unchanged review fairness."""
    def phase(item):
        return item.get("phase") or item.get("nextStep")
    def order(item):
        return (item.get("createdAt") or "", item.get("attempts") or 0,
                item.get("listingId") or "", item.get("commandId") or "")
    videos = sorted((i for i in items if phase(i) == "create_video"), key=lambda i: (
        0 if len(i.get("savedVideoAspectRatios") or
                 (i.get("videoJobClaim") or {}).get("savedVideoAspectRatios") or []) == 1 else 1,
        _hero_resume_priority(i),
        0 if (i.get("videoJobClaim") or {}).get("creativePolicy") == LITE_POLICY else 1,
        order(i),
    ))
    reviews = sorted((i for i in items if phase(i) == "review_artwork"), key=order)
    other = [i for i in items if phase(i) not in ("create_video", "review_artwork")]
    recent = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.action.in_(("create_video", "review_artwork")),
    ).order_by(CamMuseCommand.updated_at.desc()).limit(50)).all()
    outcomes = []
    for cmd in recent:
        d = cmd.details or {}
        stamp = d.get("lastCompletedAtUtc")
        completed = d.get("completedPhase")
        if stamp and completed in ("create_video", "review_artwork"):
            outcomes.append((stamp, completed))
    streak = 0
    for _, completed in sorted(outcomes, reverse=True):
        if completed == "review_artwork":
            break
        streak += 1
        if streak >= 2:
            break
    result = []
    if reviews and streak >= 2:
        result.append(reviews.pop(0))
        streak = 0
    while videos or reviews:
        if videos:
            result.append(videos.pop(0))
            streak += 1
        if reviews and (streak >= 2 or not videos):
            result.append(reviews.pop(0))
            streak = 0
    return result + other


def _muse_command_list_payload(limit: int, tenant: str, db: Session, review_only=False):
    """Read-only tenant-scoped command discovery; never claims a command."""
    # VIDEO-FIRST for the CAM Auto Video scheduler. A stale execute_task
    # command must not starve all video production when clients poll limit=1.
    # Claimable/expired-lease filtering remains in the SQL query, and generic
    # tasks remain discoverable when garment work is exhausted.
    # While global V3.3 is disabled, the two manually configured V3.3
    # pilot listings must not starve valid V3.1 action-story jobs. Keep
    # them discoverable after normal video + review work for manual canaries.
    deferred_pilots = (
        tuple(_story_v33_pilot_listings()) if not _story_v33_enabled() else ()
    )
    priority = case(
        ((CamMuseCommand.action == "create_video")
         & (~CamMuseCommand.listing_key.in_(deferred_pilots)), 0),
        (CamMuseCommand.action == "review_artwork", 1),
        (CamMuseCommand.action == "create_video", 2),
        else_=3,
    )
    rows = db.scalars(
        claimable_commands_query(tenant, _utc())
        .order_by(None)
        .order_by(
            priority, CamMuseCommand.created_at.asc(),
            CamMuseCommand.command_id.asc(),
        )
        .limit(2000)
    )
    items = []
    circuit = renderer_circuit(db, tenant)
    for cmd in rows:
        if len(items) >= limit and not smart_queue_enabled():
            break
        if (cmd.status == "claimed" and (cmd.lease_expires_at is None
                or _aware(cmd.lease_expires_at) > _utc())):
            continue
        if cmd.action == "execute_task":
            if review_only:
                continue
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
        if (phase is None or (phase == "create_video" and circuit["open"])
                or (review_only and phase != "review_artwork")):
            continue
        context = render_context(title=job.title, artwork_sha256=job.artwork_sha256)
        story_active = bool(
            phase == "create_video" and
            (_pinned_story_v33(job) or _story_v33_allowed_for_job(job))
            and _story_candidate_v33(job)
        )
        items.append({**_command_status(cmd), "phase": phase, "nextStep": phase,
                      **(_aspect_work_view(job) if phase == "create_video" else {}),
                      "placementReview": _review_placement_view(db, tenant, job) if phase == "review_artwork" else None,
                      "savedVideoAspectRatios": sorted(_verified_video_ratios(job)),
                      "videoJobClaim": (
                          _video_job_claim_contract(job)
                          if phase == "create_video" else None
                      ),
                      "effectiveVideoWorkflowVersion": (
                          STORY_VERSION if story_active else STORY_ACTION_VERSION
                      ),
                      "legacyCommandNoteOverridesStoryContract": False,
                      "title": context["renderTitle"],
                      "designTheme": context["designTheme"],
                      "renderContext": context,
                      "storyActionContract": STORY_ACTION_VERSION,
                      "placementProofContract": PLACEMENT_PROOF_VERSION,
                      "actionStoryRequired": bool(_action_story_candidate_v1(job)),
                      "reviewPath": f"/api/v1/muse-bridge/reviews?listing_id={cmd.listing_key}",
                      "jobsPath": "/api/v1/muse-bridge/jobs",
                      "videoGenerationEnabled": phase == "create_video",
                      "videoExecutor": "muse",
                      "camInternalVideoGenerationEnabled": False,
                      "requiresMusePolling": True})
        if (items[-1].get("videoJobClaim") or {}).get("creativePolicy") == LITE_POLICY:
            items[-1].update({
                "renderContext": _lite_render_context(context),
                "creativePolicy": LITE_POLICY,
                "storyActionContract": None,
                "placementProofContract": None,
                "actionStoryRequired": False,
            })
    if smart_queue_enabled():
        items = _fair_muse_discovery_order(items, tenant, db)
    items = _limit_half_open_video_candidates(items, circuit)[:limit]
    return {
        "ok": True, "items": items, "count": len(items),
        "rendererCircuit": circuit,
        "pollAfterSeconds": 20,
        "nextJobDelaySeconds": [2, 5],
        "pollAfterSecondsAppliesWhenIdle": True,
        "longPollPath": "/api/v1/muse-bridge/commands/wait?waitSeconds=20",
    }


@router.post("/workers/cooldown")
def report_muse_cooldown(
    request: Request, payload: WorkerCooldownReport,
    tenant: str = Depends(_muse),
):
    """Provider-backed per-account 429; no job is claimed or reassigned.

    Muse-01 is paused independently. A verified Muse-02 can claim new work
    through the existing atomic lease; an unverified Muse-02 cannot.
    """
    if not _muse_dual_enabled():
        raise HTTPException(409, "Muse dual-worker failover not enabled")
    try:
        state = report_provider_cooldown(
            os.environ, _muse_identity(request), provider_status=payload.providerStatus,
            retry_after_seconds=payload.retryAfterSeconds, reason=payload.reason,
        )
        routing = failover_status(os.environ)
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker cooldown persistence unavailable") from exc
    except ValueError as exc:
        raise HTTPException(422, "Invalid Muse provider cooldown") from exc
    return {
        "ok": True, "workerId": _muse_identity(request),
        "cooldown": state, "failover": routing,
        "activeVideoLeaseTransferred": False, "queuedJobsPreserved": True,
    }


@router.post("/automation/events")
def report_muse_events(
    payload: obs.WorkerEventBatch, request: Request, response: Response,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    if not obs.enabled():
        raise HTTPException(503, "Muse telemetry disabled")
    worker = _muse_identity(request)
    if not obs.INGRESS_LIMITER.admit(tenant + ":" + worker):
        raise HTTPException(429, "Muse telemetry backpressure", headers={"Retry-After": "2"})
    try:
        return obs.ingest(db, tenant, worker, payload)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(503, "Muse telemetry temporarily unavailable")


@router.get("/automation/events")
def read_muse_events(
    response: Response, limit: int = Query(default=100, ge=1, le=200),
    commandId: UUID | None = None,
    workerId: Literal["legacy", "muse-01", "muse-02"] | None = None,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok": True, "policy": obs.POLICY,
            "items": obs.timeline(db, tenant, _utc(), limit,
                                  str(commandId) if commandId else None, workerId)}


@router.get("/automation/runner-integration")
def muse_runner_integration(response: Response, tenant: str = Depends(_muse)):
    """Fixed, authenticated integration artifact, not an executable CAM command."""
    from pathlib import Path
    root = Path(__file__).resolve().parent
    source = (root / "runner_outbox.py").read_bytes()
    guide = (root / "runner_integration.md").read_text(encoding="utf-8")
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok":True,"policy":obs.POLICY,"bridgeVersion":BRIDGE_VERSION,
            "requiresAuthorizedOwnerInstall":True,"automaticExecution":False,
            "filename":"muse_observability_outbox.py","sha256":hashlib.sha256(source).hexdigest(),
            "pythonSource":source.decode("utf-8"),"integrationGuide":guide}


@router.get("/automation/diagnostics")
def muse_automation_diagnostics(
    response: Response, tenant: str = Depends(_muse),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    workers = ("muse-01", "muse-02") if _muse_dual_enabled() else ("legacy",)
    report = obs.diagnostics(db, tenant, recovery_enabled=smart_queue_enabled(),
                             worker_ids=workers, now=_utc())
    report["bridgeVersion"] = BRIDGE_VERSION
    report["rendererCircuit"] = renderer_circuit(db, tenant, _utc())
    if _muse_dual_enabled():
        for worker in report["workers"]:
            try:
                worker["cooldown"] = cooldown_status(os.environ, worker["workerId"])
                worker["executionReadiness"] = worker_execution_readiness(os.environ, worker["workerId"])
            except WorkerAccountConfigError:
                worker["cooldownStateUnavailable"] = True
    return report


@router.get("/automation/health")
def muse_automation_health(
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Read-only CAM-side evidence that the external Muse runner advances.

    CAM queues and stores assets but never executes the Muse renderer. This
    probe distinguishes live API GET polling from actual worker heartbeats.
    """
    now = _utc()
    statuses = db.execute(select(
        CamMuseCommand.status, CamMuseCommand.phase, func.count()
    ).where(
        CamMuseCommand.tenant_id == tenant
    ).group_by(CamMuseCommand.status, CamMuseCommand.phase)).all()
    count_by_status = {
        f"{status}:{phase}": n for status, phase, n in statuses
    }
    recent = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.details.is_not(None),
    ).order_by(CamMuseCommand.updated_at.desc()).limit(200)).all()
    latest_heartbeat = None
    for command in recent:
        details = command.details if isinstance(command.details, dict) else {}
        instant = details.get("lastHeartbeatAt")
        if isinstance(instant, str):
            try:
                stamp = _aware(datetime.fromisoformat(
                    instant.replace("Z", "+00:00")))
            except ValueError:
                continue
            if latest_heartbeat is None or stamp > latest_heartbeat:
                latest_heartbeat = stamp
    available = db.scalars(claimable_commands_query(
        tenant, now, actions=("create_video", "review_artwork"),
    ).limit(2000)).all()
    circuit = renderer_circuit(db, tenant, now)
    if smart_queue_enabled():
        eligible = []
        for cmd in available:
            job = db.get(CamMuseJob, (tenant, cmd.listing_key))
            if job is None:
                continue
            phase = _eligible_phase(db, tenant, job, cmd)
            if phase and not (phase == "create_video" and circuit["open"]):
                eligible.append(cmd)
        available = eligible
    queue_pending = sum(n for (s, _), n in (
        ((status, phase), count) for status, phase, count in statuses
    ) if s == "pending")
    stagnant = (bool(available)
                and (latest_heartbeat is None
                     or now - latest_heartbeat > timedelta(minutes=60)))
    return {
        "ok": True,
        "bridgeVersion": BRIDGE_VERSION,
        "externalMuseRunnerStatus": (
            "NO_RECENT_COMMAND_HEARTBEAT_WITH_READY_QUEUE"
            if stagnant else "RECENT_PROGRESS_OR_NO_READY_COMMANDS"
        ),
        "needsMuseRunnerRecovery": stagnant,
        "lastMuseCommandHeartbeatAtUtc": (
            latest_heartbeat.isoformat() if latest_heartbeat else None
        ),
        "claimableCommandsSampled": len(available),
        "readyQueueContainsCommands": bool(available),
        "pendingCommandCount": queue_pending,
        "rendererCircuit": circuit,
        "smartQueueRecoveryEnabled": smart_queue_enabled(),
        "eligibleQueueCount": len(available),
        "commandStatusCounts": count_by_status,
        "camDispatcherDoesNotRenderVideo": True,
        "camCannotUnpauseExternalMuseScheduledTask": True,
        "nextAction": (
            "Check Muse runner schedule and video quota; "
            "claim from /commands?limit=20 once renderer available. "
            "Do not replace or regenerate already saved MP4s."
            if stagnant else "Continue existing single Muse runner, no duplicate"
        ),
    }


def _bind_muse_claim_worker(result: dict, request: Request) -> dict:
    """Complete per-command claim bodies with the authenticated worker ID."""
    worker_id = _muse_identity(request)
    worker = "muse" if worker_id == "legacy" else worker_id
    if smart_queue_enabled() and getattr(request, "query_params", {}).get("view") != "full":
        result = {**result, "queueView": "compact",
                  "items": [_compact_discovery_item(item) for item in result.get("items", [])]}
    for item in result.get("items", []):
        claim = item.get("videoJobClaim") if isinstance(item, dict) else None
        if isinstance(claim, dict) and isinstance(claim.get("body"), dict):
            claim["body"] = {"worker": worker, **claim["body"]}
    return result


@router.get("/commands")
def list_muse_commands(
    request: Request,
    response: Response,
    limit: int = Query(default=10, ge=1, le=20),
    tenant: str = Depends(_muse),
    db: Session = Depends(get_db),
):
    """Authenticated read-only Muse command poll; never cache job instructions."""
    response.headers["Cache-Control"] = "private, no-store"
    denied = _muse_poll_gate(request)
    if denied is not None:
        return denied
    review_only = getattr(request.state, "muse_review_only", False)
    payload = (_muse_command_list_payload(limit, tenant, db, review_only=True)
               if review_only else _muse_command_list_payload(limit, tenant, db))
    return _bind_muse_claim_worker(payload, request)


def _read_muse_commands_for_long_poll(tenant: str, limit: int, review_only=False):
    """Use a fresh short-lived DB session per probe, not a held transaction."""
    with SessionLocal() as session:
        if review_only:
            return _muse_command_list_payload(limit, tenant, session, review_only=True)
        return _muse_command_list_payload(limit, tenant, session)


@router.get("/commands/wait")
async def wait_muse_commands(
    request: Request,
    response: Response,
    limit: int = Query(default=1, ge=1, le=20),
    wait_seconds: int = Query(default=20, ge=0, le=20, alias="waitSeconds"),
    tenant: str = Depends(_muse),
):
    """Bounded long poll for a continuously running Muse dispatcher.

    This is a read-only hint, not a claim; an authorized worker must POST /claim.
    Legacy scheduled polling remains compatible until Muse adopts this endpoint.
    """
    response.headers["Cache-Control"] = "private, no-store"
    denied = _muse_poll_gate(request)
    if denied is not None:
        return {**denied, "longPoll": True, "waitedSeconds": 0}
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + wait_seconds
    while True:
        denied = _muse_poll_gate(request)
        if denied is not None:
            return {**denied, "longPoll": True,
                    "waitedSeconds": round(loop.time() - started, 3)}
        args = (tenant, limit, True) if getattr(request.state, "muse_review_only", False) else (tenant, limit)
        result = await run_in_threadpool(_read_muse_commands_for_long_poll, *args)
        if result["items"] or loop.time() >= deadline:
            return {
                **_bind_muse_claim_worker(result, request),
                "longPoll": True,
                "waitedSeconds": round(loop.time() - started, 3),
            }
        await asyncio.sleep(min(2.0, max(0.0, deadline - loop.time())))


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


@router.post("/commands/{command_id}/soft-hold", include_in_schema=False)
def operator_soft_hold_command(
    command_id: str, payload: OperatorSoftHoldRequest,
    tenant: str = Depends(_operator), db: Session = Depends(get_db),
):
    """Temporarily skip a known blocked listing without touching its artwork.

    After the hold expires the dispatcher may issue a fresh command. This
    endpoint requires an operator credential and cannot touch a live lease.
    """
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid command ID")
    command = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if (command is None or command.action != "create_video"
            or command.listing_key != payload.listingId
            or command.status != "pending" or command.attempts != 0
            or command.lease_hash is not None
            or command.lease_expires_at is not None):
        raise HTTPException(409, "Only a matching unclaimed video command may soft-HOLD")
    job = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.listing_key == payload.listingId,
    ).with_for_update())
    if not job or job.artwork_sha256 != command.artwork_sha256:
        raise HTTPException(409, "Listing or Native Master changed")
    if (job.status == "claimed"
            and (_aware(job.lease_expires_at) is None
                 or _aware(job.lease_expires_at) > _utc())):
        raise HTTPException(409, "Cannot interrupt a live video-job lease")
    receipts = dict(job.output_receipts or {})
    if receipts.get("drive_uploads") or receipts.get("videos"):
        raise HTTPException(409, "Existing video cannot be hidden or restarted")
    now = _utc()
    until = now + timedelta(seconds=payload.holdSeconds)
    receipts["automationSoftHold"] = {
        "reason": payload.reason,
        "note": payload.note,
        "heldCommandId": command_id,
        "atUtc": now.isoformat(),
        "untilUtc": until.isoformat(),
        "preservesExistingMedia": True,
    }
    job.output_receipts = receipts
    job.updated_at = now
    command.details = {
        **(command.details if isinstance(command.details, dict) else {}),
        "softHold": {
            "reason": payload.reason,
            "note": payload.note,
            "untilUtc": until.isoformat(),
            "reopenVia": "ordinary CAM dispatcher after expiry",
        },
    }
    command.status = "failed"
    command.finished_at = now
    command.updated_at = now
    db.commit()
    return {
        "ok": True, "listingId": payload.listingId,
        "commandId": command_id, "softHoldUntilUtc": until.isoformat(),
        "queueSlotFreed": True, "priorMediaPreserved": True,
        "eligibleForFutureRequeue": True,
    }


@router.post("/commands/{command_id}/claim")
def claim_muse_command(command_id: str, request: Request,
                       x_cam_muse_policy_version: str | None = Header(default=None),
                       x_cam_story_action_contract: str | None = Header(default=None),
                       x_cam_placement_proof_contract: str | None = Header(default=None),
                       tenant: str = Depends(_muse),
                       db: Session = Depends(get_db)):
    _assert_current_muse_policy(x_cam_muse_policy_version)
    _block_legacy_claims_during_cutover()
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
    if (cmd.status == "claimed" and (cmd.lease_expires_at is None
            or _aware(cmd.lease_expires_at) > _utc())):
        raise HTTPException(409, "Command already claimed or lease requires reconciliation")
    if cmd.action == "execute_task":
        phase = "execute_task"
    else:
        job = db.scalar(select(CamMuseJob).where(
            CamMuseJob.tenant_id == tenant,
            CamMuseJob.listing_key == cmd.listing_key,
        ).with_for_update())
        if not job:
            raise HTTPException(409, "CAM listing no longer Picked")
        phase = _eligible_phase(db, tenant, job, cmd)
        if phase is None:
            raise HTTPException(409, "Artwork QA / video workflow not eligible")
        if phase == "create_video":
            circuit = renderer_circuit(db, tenant)
            if circuit["open"]:
                db.rollback()
                raise HTTPException(409, {
                    "code": "RENDERER_CIRCUIT_OPEN",
                    **circuit,
                })
        if phase == "create_video" and _fresh_action_story_v1(job):
            if (x_cam_story_action_contract != STORY_ACTION_VERSION
                    or x_cam_placement_proof_contract != PLACEMENT_PROOF_VERSION):
                raise HTTPException(
                    409, "STORY_ACTION_AND_150MM_PLACEMENT_ACK_REQUIRED: "
                    "update the Muse scheduled task before claiming this new command"
                )
        if _muse_dual_enabled():
            # Multiple pending commands for one listing must not create
            # competing renderers. Serialize via this row lock.
            competing = db.scalar(select(CamMuseCommand.command_id).where(
                CamMuseCommand.tenant_id == tenant,
                CamMuseCommand.listing_key == cmd.listing_key,
                CamMuseCommand.command_id != command_id,
                CamMuseCommand.status == "claimed",
                CamMuseCommand.lease_expires_at > _utc(),
            ).limit(1))
            if competing is not None:
                raise HTTPException(409, "Another Muse account owns this listing")
    _assert_muse_executor_ready(request, review_only=phase == "review_artwork")
    lease = secrets.token_urlsafe(36)
    cmd.phase = phase
    if phase == "create_video":
        cmd.details = {**(cmd.details or {}),
                       "requiredVideoAspectRatios": _job_required_aspects(job),
                       "videoAspectPhase": "vertical_first" if _job_required_aspects(job) == ["9:16"] else "both"}
    if _muse_dual_enabled():
        # Persist owner on the same transaction as the row-locked claim.
        # A second account gets 409, and cannot heartbeat or complete the
        # other account's command even if it sees a stale queue snapshot.
        cmd.details = {
            **(cmd.details if isinstance(cmd.details, dict) else {}),
            "museWorkerOwner": _muse_identity(request),
        }
    cmd.lease_hash = hashlib.sha256(lease.encode()).hexdigest()
    cmd.lease_expires_at = _utc() + timedelta(minutes=30)
    cmd.status = "claimed"
    cmd.attempts += 1
    cmd.updated_at = _utc()
    db.commit()
    video_claim = (
        _video_job_claim_contract(job, required_aspects=_pinned_work_aspects(cmd.details))
        if phase == "create_video" else None
    )
    if video_claim:
        worker_id = _muse_identity(request)
        video_claim["body"] = {
            "worker": "muse" if worker_id == "legacy" else worker_id,
            **video_claim["body"],
        }
    return {"ok": True, "command": _command_status(cmd),
            "rendererProbeRequired": bool(phase == "create_video" and circuit.get("probeRequired")),
            "lease": lease, "leaseExpiresAt": cmd.lease_expires_at,
            "nextStep": "execute_task" if phase == "execute_task" else
                        "reviews" if phase == "review_artwork" else "jobs",
            "task": (cmd.details if cmd.action == "execute_task" else None),
            "videoJobClaim": video_claim,
            "placementReview": _review_placement_view(db, tenant, job) if phase == "review_artwork" else None,
            "artworkSha256": cmd.artwork_sha256}


@router.post("/commands/{command_id}/yield")
def yield_muse_command_on_cooldown(
    command_id: str, payload: WorkerYieldRequest, request: Request,
    tenant: str = Depends(_muse), db: Session = Depends(get_db),
):
    """Release only an unstarted command when this account hits a real 429.

    A job whose video render has started stays leased; never allow a second
    worker to produce conflicting MP4s. Cooldown only pauses this account.
    """
    if not _muse_dual_enabled():
        raise HTTPException(404, "Two-worker cooldown handoff not enabled")
    try:
        UUID(command_id)
    except ValueError:
        raise HTTPException(404, "Invalid Muse command ID")
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if (cmd is None or cmd.status != "claimed"
            or not cmd.lease_hash or cmd.lease_expires_at is None
            or _aware(cmd.lease_expires_at) <= _utc()
            or not hmac.compare_digest(
                cmd.lease_hash, hashlib.sha256(payload.lease.encode()).hexdigest())):
        raise HTTPException(409, "Cannot yield missing, expired or foreign lease")
    _assert_muse_owner(request, cmd.details)
    if cmd.listing_key is not None:
        job = db.scalar(select(CamMuseJob).where(
            CamMuseJob.tenant_id == tenant,
            CamMuseJob.listing_key == cmd.listing_key,
        ).with_for_update())
        if (job is None or job.status == "submitted" or
                (job.status == "claimed"
                 and _aware(job.lease_expires_at) is not None
                 and _aware(job.lease_expires_at) > _utc())):
            raise HTTPException(
                409, "A video job is in progress; do not reassign its lease")
    now = _utc()
    # Record the account-level cooldown BEFORE yielding a claim. If durable
    # storage fails, retain the command lease rather than allowing Muse-01
    # to immediately reclaim from the shared queue.
    try:
        cooldown = report_provider_cooldown(
            os.environ, _muse_identity(request), provider_status=429,
            retry_after_seconds=payload.retryAfterSeconds,
            reason="provider_rate_limit",
            now=now,
        )
    except WorkerAccountConfigError as exc:
        raise HTTPException(503, "Muse worker cooldown persistence unavailable") from exc
    previous = cmd.details if isinstance(cmd.details, dict) else {}
    cmd.details = {
        **previous,
        "lastCooldown": {
            "workerId": _muse_identity(request),
            "reason": payload.reason,
            "retryAfterSeconds": payload.retryAfterSeconds,
            "cooldownUntilUtc": cooldown["cooldownUntilUtc"],
            "at": now.isoformat(),
        },
    }
    cmd.status = "pending"
    cmd.lease_hash = None
    cmd.lease_expires_at = None
    cmd.updated_at = now
    db.commit()
    return {
        "ok": True, "status": "pending", "jobWasClaimed": False,
        "returnedToCAM": True, "workerId": _muse_identity(request),
        "accountCooldownSeconds": payload.retryAfterSeconds,
        "cooldownUntilUtc": cooldown["cooldownUntilUtc"],
        "failover": failover_status(os.environ),
    }


@router.post("/commands/{command_id}/heartbeat")
def heartbeat_muse_command(
    command_id: str, payload: CommandHeartbeat, request: Request,
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
    _assert_muse_owner(request, cmd.details)
    now = _utc()
    details = dict(cmd.details) if isinstance(cmd.details, dict) else {}
    details["lastHeartbeatAt"] = now.isoformat()
    details["progressState"] = payload.state
    details["progressNote"] = payload.note or ""
    cmd.details = details
    next_expiry = now + timedelta(minutes=30)
    if cmd.phase == "create_video" and cmd.listing_key:
        # Extend the *paired video job* on BOTH single- and dual-worker
        # runtimes. Previously single-worker heartbeats extended ONLY the
        # command, so a genuine 12s two-take render could fail the final
        # video upload after its 30m job lease expired.
        # Lease expiry remains fail-closed: never resurrect an expired job.
        job = db.scalar(select(CamMuseJob).where(
            CamMuseJob.tenant_id == tenant,
            CamMuseJob.listing_key == cmd.listing_key,
        ).with_for_update())
        if job is not None and job.status == "claimed":
            _assert_muse_video_owner(request, job)
            if (_aware(job.lease_expires_at) is None
                    or _aware(job.lease_expires_at) <= now):
                raise HTTPException(409, "Muse video job lease already expired")
            job.lease_expires_at = next_expiry
            job.updated_at = now
    cmd.lease_expires_at = next_expiry
    cmd.updated_at = now
    db.commit()
    return {"ok": True, "commandId": command_id,
            "status": cmd.status, "progressState": payload.state,
            "leaseExpiresAt": cmd.lease_expires_at.isoformat()}


@router.post("/commands/{command_id}/complete")
def complete_muse_command(command_id: str, payload: CommandAck,
                          request: Request, tenant: str = Depends(_muse),
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
    _assert_muse_owner(request, cmd.details)
    job = None if cmd.action == "execute_task" else db.scalar(
        select(CamMuseJob).where(
            CamMuseJob.tenant_id == tenant,
            CamMuseJob.listing_key == cmd.listing_key,
        ).with_for_update()
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
        request.state.muse_business_result = {"outcome": payload.outcome, "phase": "execute_task", "commandStatus": cmd.status}
        return {"ok": True, **_command_status(cmd)}
    completed_phase = cmd.phase
    retry_marker = None
    transient_status = None
    if payload.outcome == "failed" and cmd.phase == "create_video":
        transient_status = (
            payload.rendererHttpStatus
            if payload.rendererErrorStage == "generate_video" and payload.rendererHttpStatus
            and not any(code in payload.notes.upper() for code in _RENDERER_HARD_BLOCKERS)
            else _reported_renderer_transient_status(payload.notes)
        )
    if payload.outcome == "failed":
        cmd.status = "failed"
        cmd.finished_at = _utc()
        if (cmd.phase == "create_video" and job is not None
                and job.artwork_sha256 == cmd.artwork_sha256
                and (job.status == "pending" or
                     (job.status == "claimed" and job.lease_hash
                      and _aware(job.lease_expires_at) is not None))):
            # A failed, completed command has no ongoing render owner.
            # Release its paired job lease immediately (same row-locked
            # transaction), preserving every uploaded hero and video receipt.
            # Never auto-reassign a terminal renderer capability refusal.
            if job.status == "claimed":
                _assert_muse_video_owner(request, job)
            receipts = dict(job.output_receipts) if isinstance(job.output_receipts, dict) else {}
            reported_blocker = next(
                (code for code in _RENDERER_HARD_BLOCKERS
                 if code in payload.notes.upper()), None
            )
            if reported_blocker is not None:
                receipts["videoCapabilityHold"] = {
                    "code": reported_blocker,
                    "reportedAt": _utc().isoformat(),
                    "reviewRequired": True,
                    "noAutomaticRenderRetry": True,
                }
            else:
                if transient_status is not None:
                    retry_marker = _schedule_transient_render_retry(
                        receipts, cmd, job, transient_status, _utc()
                    )
                    if retry_marker is not None:
                        # Keep the existing request/command identity; the
                        # retry is discoverable automatically after backoff.
                        cmd.status = "pending"
                        cmd.finished_at = None
            job.output_receipts = receipts
            job.status = "pending"
            job.lease_hash = None
            job.lease_expires_at = None
            job.updated_at = _utc()
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
        required = _pinned_work_aspects(cmd.details)
        if len(required) == 1:
            if (not job or job.artwork_sha256 != cmd.artwork_sha256
                    or required[0] not in _verified_video_ratios(job)
                    or job.status not in ("claimed", "pending", "submitted", "completed")):
                raise HTTPException(409, "Required video receipt not yet verified")
            if job.status == "claimed":
                _assert_muse_video_owner(request, job)
                scope = receipts.get("videoWorkScope") or {}
                if scope.get("commandId") != cmd.command_id:
                    raise HTTPException(409, "Video job is not paired with this command")
                job.status = "pending"
                job.lease_hash = None
                job.lease_expires_at = None
                job.updated_at = _utc()
            cmd.details = {**(cmd.details or {}),
                           "completedVideoAspectRatios": required,
                           "allVideoAspectsComplete": _verified_video_ratios(job) == set(VIDEO_ASPECTS),
                           "deferredVideoAspectRatios": [r for r in VIDEO_ASPECTS
                               if r not in required and r not in _verified_video_ratios(job)]}
        else:
            videos = receipts.get("videos")
            if (not job or job.status != "submitted"
                    or not isinstance(videos, list) or len(videos) != 2
                    or {x.get("aspectRatio") for x in videos
                        if isinstance(x, dict)} != {"9:16", "16:9"}):
                raise HTTPException(409, "Two video receipts not yet submitted to CAM")
        cmd.status = "receipt_submitted"
        cmd.finished_at = _utc()
    cmd.details = {
        **(cmd.details if isinstance(cmd.details, dict) else {}),
        "outcome": payload.outcome, "notes": payload.notes[:800],
        "completedPhase": completed_phase,
        "lastCompletedAtUtc": _utc().isoformat(),
        "videoReceiptNotCamLinked": not bool(
            job is not None and (job.output_receipts or {}).get("camLinked")
        ),
        "renderRetry": retry_marker,
        **({"lastRendererTransientFailure": {
            "httpStatus": transient_status, "atUtc": _utc().isoformat(),
            "providerStatusIndependentlyVerified": False,
        }} if transient_status is not None else {}),
    }
    cmd.lease_hash = None
    cmd.lease_expires_at = None
    cmd.updated_at = _utc()
    db.commit()
    request.state.muse_business_result = {
        "outcome": payload.outcome, "phase": completed_phase, "commandStatus": cmd.status,
        "listingId": cmd.listing_key, "providerHttpStatus": transient_status,
        "retryScheduled": retry_marker is not None,
        "retryAfterSeconds": retry_marker.get("retryDelaySeconds") if retry_marker else None,
        "capabilityHold": bool(job is not None and (job.output_receipts or {}).get("videoCapabilityHold")),
    }
    return {
        "ok": True, **_command_status(cmd),
        "retryScheduled": retry_marker is not None,
        "renderRetry": retry_marker,
    }