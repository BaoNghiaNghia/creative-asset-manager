"""Disabled-by-default Windows Image Agent transport for CAM Etsy Picked jobs.

Uses a dedicated credential role inside existing Etsy Spy agent storage; the
ordinary Scout credential cannot claim image jobs, and this role cannot sync
Scout data. Jobs are external-only: Codex CLI processors never claim them.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.processing.model import ProcessingJobModel
from .extraction import EXTRACTION_JOB_TYPE, EXTRACTION_ENGINE, extraction_enabled
from .model import EtsySpyAgent, EtsySpyPicked, utcnow
from .service import (
    IMAGE_AGENT_NAME_PREFIX, authenticate_agent, create_agent, text,
)

image_router = APIRouter(prefix="/image-agents", tags=["etsy-image-agent"])
READ = require_permission("etsy_spy.read")
MANAGE = require_permission("etsy_spy.manage")
MAX_RECEIPT_BYTES = 31_000_000
MAX_PNG_BYTES = 22_000_000
# Default storage policy holds a small local review buffer, not 200/day forever.
DEFAULT_STORAGE_QUOTA_BYTES = 2 * (1024 ** 3)
DEFAULT_STORAGE_FREE_FLOOR_BYTES = 3 * (1024 ** 3)
VN_TIMEZONE = timezone(timedelta(hours=7))


def _storage_limit(env_name: str, default: int) -> int:
    """Bound admin-provided capacity settings; never allow zero/unlimited."""
    try:
        value = int(os.getenv(env_name, str(default)))
    except ValueError:
        return default
    return max(MAX_PNG_BYTES, min(value, 500 * 1024 ** 3))


def storage_capacity(incoming_bytes: int = 0) -> dict:
    """Fail closed if persistent storage is missing, full or past its quota.

    No automatic deletion of selected designs: operators must explicitly archive
    or add capacity. The API user needs read/write permission on the root.
    """
    root_text = os.getenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", "").strip()
    quota = _storage_limit(
        "CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES", DEFAULT_STORAGE_QUOTA_BYTES)
    floor = _storage_limit(
        "CAM_ETSY_EXTRACTION_MIN_FREE_BYTES", DEFAULT_STORAGE_FREE_FLOOR_BYTES)
    status = {"ready": False, "reason": "storage_not_provisioned",
              "used_bytes": 0, "quota_bytes": quota, "free_bytes": None}
    if not root_text or not Path(root_text).is_absolute():
        return status
    root = Path(root_text)
    if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        return status
    try:
        free = shutil.disk_usage(root).free
        # Count only extraction PNGs and interrupted atomic uploads, not
        # unrelated tenant files or data outside the dedicated storage root.
        used = sum(file.stat().st_size for file in root.glob("*/*")
                   if file.is_file() and file.suffix in {".png", ".pending"})
    except OSError:
        return {**status, "reason": "storage_unavailable"}
    over_quota = (used >= quota if incoming_bytes == 0
                  else used + incoming_bytes > quota)
    reason = ("storage_quota_reached" if over_quota else
              "disk_headroom_low" if free - incoming_bytes < floor else "ready")
    return {"ready": reason == "ready", "reason": reason,
            "used_bytes": used, "quota_bytes": quota, "free_bytes": free}


def daily_cap() -> int:
    """Bound daily outbound claims to the intended 200/day pilot capacity."""
    raw = os.getenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "200")
    try:
        value = int(raw)
    except ValueError:
        return 200
    return max(1, min(1000, value))


def daily_start_utc() -> datetime:
    local = datetime.now(VN_TIMEZONE)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def claimed_today(db: Session, tenant: str) -> int:
    return int(db.scalar(select(func.count()).select_from(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.claimed_at >= daily_start_utc(),
    )) or 0)


def extraction_overview(db: Session, tenant: str) -> dict:
    counts = dict(db.execute(select(ProcessingJobModel.status, func.count()).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.cancellation_requested.is_(False),
    ).group_by(ProcessingJobModel.status)).all())
    completed_today = db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.status == "completed",
        ProcessingJobModel.completed_at >= daily_start_utc(),
    ).order_by(desc(ProcessingJobModel.completed_at)).limit(1000)).all()
    result_counts = {"needs_review": 0, "needs_ai": 0, "failed": 0}
    for job in completed_today:
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        status = result.get("status")
        if status in result_counts:
            result_counts[status] += 1
    today = claimed_today(db, tenant)
    limit = daily_cap()
    return {
        "enabled": transport_enabled(),
        "daily_cap": limit,
        "claimed_today": today,
        "remaining_today": max(0, limit - today),
        "queued": int(counts.get("pending", 0) + counts.get("retry", 0)),
        "processing": int(counts.get("processing", 0)),
        "completed_today": len(completed_today),
        "needs_review_today": result_counts["needs_review"],
        "needs_ai_today": result_counts["needs_ai"],
        "failed_today": result_counts["failed"],
        "cancellation_excluded": True,
        "timezone": "Asia/Ho_Chi_Minh",
        "storage": storage_capacity(),
    }


def transport_enabled() -> bool:
    return extraction_enabled()


def orchestrator_enabled() -> bool:
    # Only newly leased jobs may opt into v1. Legacy leases stay unchanged.
    return os.getenv("CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED", "0") == "1"


def _enabled() -> None:
    if not transport_enabled():
        raise HTTPException(503, "Etsy extraction worker transport is disabled")
    location = os.getenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", "")
    if not location or not Path(location).is_absolute():
        raise HTTPException(503, "Persistent extraction asset storage is not configured")


def _authenticate(db: Session, agent_id: str, authorization: str | None):
    return authenticate_agent(db, agent_id, authorization, image_only=True)


def _lease_token(agent: EtsySpyAgent, job: ProcessingJobModel) -> str:
    message = f"{agent.id}:{job.id}:{job.attempt_count}".encode("ascii")
    return hmac.new(bytes.fromhex(agent.token_hash), message, "sha256").hexdigest()


def _task(job: ProcessingJobModel, agent: EtsySpyAgent) -> dict:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    return {
        "task": {
            "id": job.id, "listing_key": job.entity_id, "tenant_id": agent.tenant_id,
            "engine": "auto", "source": payload.get("source"),
            "rights_status": "unverified",
            "manual_qa_required": True,
            **({"execution_mode": "orchestrator_v1"}
               if payload.get("execution_mode") == "orchestrator_v1" else {}),
        },
        "lease_token": _lease_token(agent, job),
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
    }


async def _bounded_json(request: Request, limit: int) -> dict:
    if int(request.headers.get("content-length", "0") or "0") > limit:
        raise HTTPException(413, "Receipt payload too large")
    size = 0
    data = bytearray()
    async for piece in request.stream():
        size += len(piece)
        if size > limit:
            raise HTTPException(413, "Receipt payload too large")
        data.extend(piece)
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid JSON") from None
    if not isinstance(parsed, dict):
        raise HTTPException(422, "JSON object required")
    return parsed


def _storage_path(tenant: str, job_id: str) -> Path:
    configured = os.getenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", "")
    root = Path(configured)
    if not configured or not root.is_absolute():
        raise HTTPException(503, "Persistent Etsy extraction storage is not configured")
    tenant_safe = hashlib.sha256(tenant.encode()).hexdigest()[:24]
    # Job IDs are generated server-side UUIDs, never user-provided paths.
    try:
        UUID(job_id)
    except ValueError:
        raise HTTPException(422, "Invalid job ID") from None
    return root / tenant_safe / (job_id + ".png")


def _verify_png(raw: bytes) -> None:
    """Validate bounded RGBA PNG chunk structure and CRC before storing.

    Full pixel decoding and visual fidelity remain a separate manual QA gate.
    """
    if len(raw) < 57 or len(raw) > MAX_PNG_BYTES:
        raise HTTPException(422, "Invalid PNG size")
    if raw[:8] != bytes([137, 80, 78, 71, 13, 10, 26, 10]):
        raise HTTPException(422, "PNG signature invalid")
    position, found_image, found_end = 8, False, False
    first = True
    while position + 12 <= len(raw):
        length = int.from_bytes(raw[position:position + 4], "big")
        end = position + 12 + length
        if length > MAX_PNG_BYTES or end > len(raw):
            raise HTTPException(422, "Malformed PNG chunk size")
        kind = raw[position + 4:position + 8]
        data = raw[position + 8:position + 8 + length]
        observed = int.from_bytes(raw[position + 8 + length:end], "big")
        expected = binascii.crc32(kind + data) & 0xFFFFFFFF
        if observed != expected:
            raise HTTPException(422, "PNG chunk CRC invalid")
        if first:
            if kind != b"IHDR" or length != 13:
                raise HTTPException(422, "PNG IHDR missing")
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            if (width, height) != (4500, 5400) or data[8:13] != bytes([8, 6, 0, 0, 0]):
                raise HTTPException(422, "Output must be RGBA 4500x5400 8-bit")
        if kind == b"IDAT":
            found_image = True
        if kind == b"IEND":
            if length != 0 or end != len(raw):
                raise HTTPException(422, "PNG IEND invalid")
            found_end = True
            break
        first = False
        position = end
    if not (found_image and found_end):
        raise HTTPException(422, "PNG image chunks incomplete")


REVIEW_FLAGS = {"low_native_resolution", "upscaled_preview", "edge_noise"}


def _validated_review_evidence(payload: dict, status: str) -> tuple[str | None, dict | None]:
    """Bound optional worker QA metadata. Never infer print or IP clearance."""
    reason = payload.get("reason")
    if reason is not None and (
        not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{2,72}", reason)
    ):
        raise HTTPException(422, "Invalid extraction reason")
    raw = payload.get("qa")
    if raw is None:
        return reason, None  # Older journal receipts remain replayable.
    required = {"source_resolution", "cropped_artwork", "foreground_coverage",
                "border_noise", "upscale_factor", "review_flags",
                "print_ready", "ocr_verified", "rights_verified"}
    if status != "needs_review" or not isinstance(raw, dict) or set(raw) != required:
        raise HTTPException(422, "Unexpected extraction QA fields")
    for field in ("print_ready", "ocr_verified", "rights_verified"):
        if raw[field] is not False:
            raise HTTPException(422, "Unverified source cannot be print or rights cleared")
    dimensions = {}
    for field in ("source_resolution", "cropped_artwork"):
        value = raw[field]
        if (not isinstance(value, list) or len(value) != 2 or
            any(type(item) is not int or item < 1 or item > 32000 for item in value)):
            raise HTTPException(422, "Invalid original extraction dimensions")
        dimensions[field] = value
    if any(dimensions["cropped_artwork"][i] > dimensions["source_resolution"][i]
           for i in range(2)):
        raise HTTPException(422, "Original artwork exceeds source dimensions")
    coverage, upscale = raw["foreground_coverage"], raw["upscale_factor"]
    if (type(coverage) not in (int, float) or not math.isfinite(coverage) or
        not 0 <= coverage <= 1 or type(upscale) not in (int, float) or
        not math.isfinite(upscale) or not 0 < upscale <= 1000):
        raise HTTPException(422, "Invalid extraction confidence metrics")
    noise, flags = raw["border_noise"], raw["review_flags"]
    if type(noise) is not int or not 0 <= noise <= 255:
        raise HTTPException(422, "Invalid image border noise")
    if (not isinstance(flags, list) or len(flags) > len(REVIEW_FLAGS) or
        any(flag not in REVIEW_FLAGS for flag in flags) or len(set(flags)) != len(flags)):
        raise HTTPException(422, "Untrusted review flag")
    return reason, {
        **dimensions, "foreground_coverage": float(coverage),
        "border_noise": noise, "upscale_factor": float(upscale),
        "review_flags": sorted(flags), "print_ready": False,
        "ocr_verified": False, "rights_verified": False,
    }


ORCHESTRATOR_QA_FIELDS = {
    "schema", "method", "width", "height", "dpi",
    "alpha_pixel_verified", "transparent_fraction",
    "border_transparent_fraction", "qa_status", "review_flags",
    "manual_text_review_required", "manual_artwork_review_required",
    "print_ready", "ocr_verified", "rights_verified",
}


def _validated_orchestrator_evidence(
    payload: dict, status: str, engine: str
) -> tuple[str | None, dict]:
    """Authenticated, pinned-v1 PNG provenance. This is *not* commercial approval."""
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or
                               not re.fullmatch(r"[a-z0-9_]{2,72}", reason)):
        raise HTTPException(422, "Invalid orchestrator reason")
    qa = payload.get("qa")
    if (status != "needs_review" or not isinstance(qa, dict) or
        set(qa) != ORCHESTRATOR_QA_FIELDS or
        qa.get("schema") != "cam-image-orchestrator-qa-v1" or
        qa.get("method") != engine):
        raise HTTPException(422, "Orchestrator review evidence required")
    if (any(qa.get(name) is not False for name in
            ("print_ready", "ocr_verified", "rights_verified")) or
        qa.get("alpha_pixel_verified") is not True or
        qa.get("manual_artwork_review_required") is not True or
        type(qa.get("manual_text_review_required")) is not bool or
        (engine == "chatgpt_assisted" and
         qa.get("manual_text_review_required") is not True)):
        raise HTTPException(422, "Orchestrator cannot approve artwork or rights")
    if (type(qa.get("width")) is not int or qa["width"] != 4500 or
        type(qa.get("height")) is not int or qa["height"] != 5400 or
        type(qa.get("dpi")) is not int or qa["dpi"] != 300 or
        qa.get("qa_status") != "needs_review"):
        raise HTTPException(422, "Orchestrator output must be review-only print-size PNG")
    fractions = {}
    for key, minimum in (("transparent_fraction", 0.02),
                         ("border_transparent_fraction", 0.55)):
        value = qa[key]
        if (type(value) not in (float, int) or not math.isfinite(value) or
            not minimum <= value <= 1):
            raise HTTPException(422, "Unverified transparent background")
        fractions[key] = float(value)
    flags = qa["review_flags"]
    if (not isinstance(flags, list) or len(flags) > 8 or
        any(not isinstance(x, str) or
            not re.fullmatch(r"[a-z_]{2,40}", x) for x in flags) or
        len(set(flags)) != len(flags)):
        raise HTTPException(422, "Invalid orchestrator review flags")
    return reason, {**qa, **fractions, "review_flags": sorted(flags)}


@image_router.post("", status_code=201)
async def pair_image_agent(
    request: Request, principal: CurrentPrincipal = Depends(MANAGE),
    db: Session = Depends(get_db),
):
    data = await _bounded_json(request, 4096)
    label = text(data.get("name"), 64, required=True)
    agent, token = create_agent(db, principal.active_tenant_id,
                                IMAGE_AGENT_NAME_PREFIX + label)
    return {"agent_id": agent.id, "token": token,
            "tenant_id": principal.active_tenant_id,
            "notice": "Dedicated Image Agent secret. Save on the Windows D: drive only."}


@image_router.get("")
def list_image_agents(principal: CurrentPrincipal = Depends(READ),
                      db: Session = Depends(get_db)):
    agents = db.scalars(select(EtsySpyAgent).where(
        EtsySpyAgent.tenant_id == principal.active_tenant_id,
        EtsySpyAgent.name.startswith(IMAGE_AGENT_NAME_PREFIX),
    ).order_by(desc(EtsySpyAgent.created_at))).all()
    return {"items": [
        {"id": a.id, "name": a.name, "active": a.active,
         "last_seen_at": a.last_seen_at} for a in agents
    ]}


@image_router.get("/overview")
def read_image_agent_overview(principal: CurrentPrincipal = Depends(READ),
                              db: Session = Depends(get_db)):
    """Tenant-scoped 200/day extraction pilot metrics; safe when worker is off."""
    return {**extraction_overview(db, principal.active_tenant_id),
            "orchestrator_enabled": orchestrator_enabled(),
            "orchestrator_contract": "v1"}


@image_router.get("/{agent_id}/probe")
def probe_image_agent(
    agent_id: str, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Read-only transport handshake; valid even when extraction is disabled.

    No claims, listing reads, PNG transfers, or backfill are triggered. Dedicated
    device credentials cannot access Scout APIs with this role.
    """
    agent = _authenticate(db, agent_id, authorization)
    agent.last_seen_at = utcnow()
    db.commit()
    return {
        "paired": True,
        "agent_id": agent.id,
        "tenant_id": agent.tenant_id,
        "transport_enabled": transport_enabled(),
        "storage_ready": storage_capacity()["ready"],
        "daily_cap": daily_cap(),
        "claimed_today": claimed_today(db, agent.tenant_id),
        "lease_protocol": "v1",
        "orchestrator_enabled": orchestrator_enabled(),
        "orchestrator_contract": "v1",
    }


@image_router.post("/{agent_id}/claim")
def claim_image_job(
    agent_id: str, authorization: str | None = Header(None),
    x_cam_image_worker: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = _authenticate(db, agent_id, authorization)
    _enabled()
    requested_orchestrator = x_cam_image_worker == "orchestrator-v1"
    worker = "etsy-image:" + agent.id
    now = utcnow()
    # If HTTP response was lost, return the same task + lease. Never create a
    # second attempt or claim a new task while the original remains unresolved.
    active = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == agent.tenant_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.claimed_by == worker,
        ProcessingJobModel.status == "processing",
        ProcessingJobModel.lease_expires_at > now,
        ProcessingJobModel.cancellation_requested.is_(False),
    ).order_by(ProcessingJobModel.claimed_at.desc()).limit(1))
    if active is not None:
        mode = ((active.payload_json or {}).get("execution_mode")
                if isinstance(active.payload_json, dict) else None)
        if (mode == "orchestrator_v1") != requested_orchestrator:
            return {"task": None, "pause_reason": "existing_mode_task_requires_completion"}
        return _task(active, agent)
    if requested_orchestrator and not orchestrator_enabled():
        return {"task": None, "pause_reason": "orchestrator_not_enabled"}
    if orchestrator_enabled() and not requested_orchestrator:
        return {"task": None, "pause_reason": "orchestrator_device_upgrade_required"}
    # Conservative per-tenant cap for the requested ~200/day workload.
    # Existing leased work can still renew through /check-job.
    if claimed_today(db, agent.tenant_id) >= daily_cap():
        agent.last_seen_at = now
        db.commit()
        return {"task": None, "pause_reason": "daily_cap_reached"}
    capacity = storage_capacity()
    if not capacity["ready"]:
        agent.last_seen_at = now
        db.commit()
        return {"task": None, "pause_reason": capacity["reason"]}
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == agent.tenant_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.provider_key == EXTRACTION_ENGINE,
        or_(
            and_(
                ProcessingJobModel.status.in_(("pending", "retry")),
                ProcessingJobModel.next_attempt_at <= now,
            ),
            and_(
                ProcessingJobModel.status == "processing",
                ProcessingJobModel.lease_expires_at <= now,
            ),
        ),
        ProcessingJobModel.cancellation_requested.is_(False),
        ProcessingJobModel.attempt_count < ProcessingJobModel.max_attempts,
        select(EtsySpyPicked.listing_key).where(
            EtsySpyPicked.tenant_id == agent.tenant_id,
            EtsySpyPicked.listing_key == ProcessingJobModel.entity_id,
        ).exists(),
    ).order_by(desc(ProcessingJobModel.priority), ProcessingJobModel.created_at)
        .with_for_update(skip_locked=True).limit(1))
    if job is None:
        agent.last_seen_at = utcnow()
        db.commit()
        return {"task": None}
    job.status = "processing"
    job.claimed_by = worker
    job.claimed_at = now
    job.lease_expires_at = now + timedelta(minutes=30)
    job.attempt_count += 1
    if requested_orchestrator:
        job.payload_json = {**(job.payload_json or {}),
                            "execution_mode": "orchestrator_v1"}
    agent.last_seen_at = job.claimed_at
    db.commit()
    return _task(job, agent)


@image_router.post("/{agent_id}/check-job")
async def check_and_renew_image_job(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Bounded, replay-safe lease check before extraction/upload.

    The same authorized device may renew its old active lease after an outage;
    another agent cannot steal the task or replace a completed receipt.
    """
    agent = _authenticate(db, agent_id, authorization)
    _enabled()
    body = await _bounded_json(request, 4096)
    job_id = str(body.get("job_id") or "")
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
    ).with_for_update())
    if (job is None or job.tenant_id != agent.tenant_id or
        job.job_type != EXTRACTION_JOB_TYPE):
        raise HTTPException(404, "Image job not found")
    lease = str(body.get("lease_token") or "")
    if not hmac.compare_digest(lease, _lease_token(agent, job)):
        raise HTTPException(409, "Stale image job lease")
    if job.cancellation_requested or db.get(
        EtsySpyPicked, (agent.tenant_id, job.entity_id)
    ) is None:
        return {"status": "cancelled", "job_id": job.id}
    if job.status == "completed":
        result = (job.payload_json or {}).get("result") or {}
        return {
            "status": "completed", "job_id": job.id,
            "receipt_id": result.get("receipt_id"),
            "result_status": result.get("status"),
            "output_sha256": result.get("output_sha256"),
        }
    if job.status == "failed":
        return {"status": "failed", "job_id": job.id}
    if job.status != "processing" or job.claimed_by != "etsy-image:" + agent.id:
        raise HTTPException(409, "Image job is no longer owned by this device")
    job.lease_expires_at = utcnow() + timedelta(minutes=30)
    agent.last_seen_at = utcnow()
    db.commit()
    return {"status": "processing", "job_id": job.id,
            "lease_expires_at": job.lease_expires_at.isoformat()}


@image_router.post("/{agent_id}/result")
async def submit_image_result(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = _authenticate(db, agent_id, authorization)
    _enabled()
    data = await _bounded_json(request, MAX_RECEIPT_BYTES)
    job_id = str(data.get("job_id") or "")
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
    ).with_for_update())
    if (job is None or job.tenant_id != agent.tenant_id or
        job.job_type != EXTRACTION_JOB_TYPE):
        raise HTTPException(404, "Image extraction job not found")
    status = data.get("status")
    if status not in {"needs_review", "needs_ai", "failed"}:
        raise HTTPException(422, "Invalid extraction status")
    engine = data.get("engine")
    orchestrated = (isinstance(job.payload_json, dict) and
                    job.payload_json.get("execution_mode") == "orchestrator_v1")
    allowed_engines = ({"fast_cpu", "ai_review", "chatgpt_assisted"}
                       if orchestrated else {"fast_cpu", "ai_review"})
    if engine not in allowed_engines:
        raise HTTPException(422, "Invalid extraction engine")
    if status == "needs_review" and engine not in (
        {"fast_cpu", "chatgpt_assisted"} if orchestrated else {"fast_cpu"}
    ):
        raise HTTPException(422, "Review PNG engine not authorized for this lease")
    if status == "needs_ai" and engine != "ai_review":
        raise HTTPException(422, "AI fallback must be routed to manual review")
    lease = str(data.get("lease_token") or "")
    if not hmac.compare_digest(lease, _lease_token(agent, job)):
        raise HTTPException(403, "Stale or invalid agent lease")
    source_sha256 = str(data.get("source_sha256") or "")
    if source_sha256 and not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise HTTPException(422, "Invalid source hash")
    reason, qa = (_validated_orchestrator_evidence(data, status, engine)
                  if orchestrated and status == "needs_review"
                  else _validated_review_evidence(data, status))
    if orchestrated and status == "needs_review" and not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise HTTPException(422, "Orchestrated source SHA256 required")
    previous = (job.payload_json or {}).get("result")
    if job.status == "completed" and isinstance(previous, dict):
        if (previous.get("status") == status and
            previous.get("output_sha256") == data.get("output_sha256")):
            return {"ok": True, "receipt_id": previous["receipt_id"],
                    "output_sha256": previous.get("output_sha256")}
        raise HTTPException(409, "A different result was already accepted")
    if (job.claimed_by != "etsy-image:" + agent.id or
        job.status != "processing" or job.cancellation_requested):
        raise HTTPException(409, "Job is not owned or has been cancelled")
    lease_deadline = job.lease_expires_at
    if lease_deadline is not None and lease_deadline.tzinfo is None:
        lease_deadline = lease_deadline.replace(tzinfo=timezone.utc)
    if lease_deadline is None or lease_deadline < utcnow():
        raise HTTPException(409, "Job lease expired; contact operator")
    output_hash = None
    if status == "needs_review":
        raw_b64 = data.get("png_base64")
        if not isinstance(raw_b64, str) or len(raw_b64) > MAX_RECEIPT_BYTES:
            raise HTTPException(422, "PNG bytes required for review")
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "PNG base64 invalid") from None
        _verify_png(raw)
        output_hash = hashlib.sha256(raw).hexdigest()
        if output_hash != data.get("output_sha256"):
            raise HTTPException(422, "PNG SHA256 mismatch")
        target = _storage_path(agent.tenant_id, job.id)
        # Result endpoint holds the ProcessingJob row lock (PostgreSQL), so
        # retries do not delete an upload currently owned by another request.
        if target.is_file():
            if hashlib.sha256(target.read_bytes()).hexdigest() != output_hash:
                raise HTTPException(409, "Immutable PNG revision mismatch")
        else:
            pending = target.with_suffix(".pending")
            if pending.is_file():
                # Recover a crash after fsync but before atomic rename. A
                # truncated temporary file can be replaced by the exact same
                # authenticated result; no different PNG is accepted.
                if hashlib.sha256(pending.read_bytes()).hexdigest() == output_hash:
                    pending.replace(target)
                else:
                    pending.unlink()
            if not target.is_file():
                capacity = storage_capacity(len(raw))
                if not capacity["ready"]:
                    raise HTTPException(507, "Review storage unavailable or over capacity")
                target.parent.mkdir(parents=True, exist_ok=True)
                with pending.open("xb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                pending.replace(target)
    elif data.get("png_base64") or data.get("output_sha256"):
        raise HTTPException(422, "Non-PNG status must not include an output")
    receipt_id = hashlib.sha256(
        f"{agent.tenant_id}:{job.id}:{status}:{output_hash or ''}".encode()
    ).hexdigest()[:40]
    result = {
        "status": status, "engine": engine, "source_sha256": source_sha256,
        "output_sha256": output_hash, "receipt_id": receipt_id,
        "print_ready": False, "manual_qa_required": True,
        "rights_status": "unverified", "finished_at": utcnow().isoformat(),
    }
    if reason is not None:
        result["reason"] = reason
    if qa is not None:
        result["qa"] = qa
    job.payload_json = {**(job.payload_json or {}), "result": result}
    job.status = "completed"
    job.completed_at = utcnow()
    job.lease_expires_at = None
    agent.last_seen_at = utcnow()
    db.commit()
    return {"ok": True, "receipt_id": receipt_id, "output_sha256": output_hash}



CANDIDATE_METHOD = "colour_layer_preview_not_ai"


def _validated_candidate_metrics(payload: dict) -> dict:
    """Validate review-only provenance, never AI/print/right clearance."""
    qa = payload.get("qa")
    if not isinstance(qa, dict):
        raise HTTPException(422, "Candidate QA metadata required")
    for key in ("manual_visual_review_required", "not_an_ai_model"):
        if qa.get(key) is not True:
            raise HTTPException(422, "Candidate must require manual review")
    for key in ("print_ready", "rights_verified", "ocr_verified"):
        if qa.get(key) is not False:
            raise HTTPException(422, "Candidate cannot claim print or rights approval")
    metrics = {}
    for key in ("source_width", "source_height",
                "native_artwork_width", "native_artwork_height"):
        value = qa.get(key)
        if type(value) is not int or not 1 <= value <= 32000:
            raise HTTPException(422, "Invalid candidate dimensions")
        metrics[key] = value
    if (metrics["native_artwork_width"] > metrics["source_width"] or
        metrics["native_artwork_height"] > metrics["source_height"]):
        raise HTTPException(422, "Recovered artwork exceeds original source")
    factor = qa.get("upscale_factor")
    if (type(factor) not in (float, int) or not math.isfinite(factor)
        or not 0.1 <= factor <= 100):
        raise HTTPException(422, "Invalid candidate upscaling metric")
    foreground = qa.get("foreground_pixels")
    if type(foreground) is not int or not 1 <= foreground <= 32000 * 32000:
        raise HTTPException(422, "Invalid candidate alpha mask")
    return {**metrics, "upscale_factor": round(float(factor), 2),
            "foreground_pixels": foreground,
            "print_ready": False, "rights_verified": False,
            "ocr_verified": False, "manual_visual_review_required": True,
            "not_an_ai_model": True}


@image_router.post("/{agent_id}/review-candidate")
async def submit_review_candidate(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Store an isolated, unapproved PNG candidate for a previously completed needs_ai job.

    Never replaces the original immutable Fast Extraction receipt; does not
    claim another job or increment the daily quota. The exact original paired
    device and SHA-256 source are required.
    """
    agent = _authenticate(db, agent_id, authorization)
    data = await _bounded_json(request, MAX_RECEIPT_BYTES)
    job_id = str(data.get("job_id") or "")
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
    ).with_for_update())
    if (job is None or job.tenant_id != agent.tenant_id or
        job.job_type != EXTRACTION_JOB_TYPE):
        raise HTTPException(404, "Extraction job not found")
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    original = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if (job.status != "completed" or original.get("status") != "needs_ai" or
        job.claimed_by != "etsy-image:" + agent.id or
        job.cancellation_requested or
        db.get(EtsySpyPicked, (agent.tenant_id, job.entity_id)) is None):
        raise HTTPException(409, "Original needs_ai job is not available for review")
    if data.get("method") != CANDIDATE_METHOD:
        raise HTTPException(422, "Unsupported local review candidate method")
    source_sha = str(data.get("source_sha256") or "")
    if (not re.fullmatch(r"[0-9a-f]{64}", source_sha) or
        source_sha != original.get("source_sha256")):
        raise HTTPException(422, "Candidate source SHA256 does not match original")
    qa = _validated_candidate_metrics(data)
    output_sha = str(data.get("output_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", output_sha):
        raise HTTPException(422, "Invalid candidate SHA256")
    old = payload.get("review_candidate")
    if isinstance(old, dict) and old.get("output_sha256") != output_sha:
        raise HTTPException(409, "Candidate revision is immutable")
    raw_b64 = data.get("png_base64")
    if not isinstance(raw_b64, str) or len(raw_b64) > MAX_RECEIPT_BYTES:
        raise HTTPException(422, "Candidate PNG required")
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Candidate base64 malformed") from None
    _verify_png(raw)
    if hashlib.sha256(raw).hexdigest() != output_sha:
        raise HTTPException(422, "Candidate PNG SHA256 mismatch")
    target = _storage_path(agent.tenant_id, job.id).with_suffix(".candidate.png")
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != output_sha:
            raise HTTPException(409, "Candidate storage revision mismatch")
    else:
        pending = target.with_suffix(".pending")
        if pending.is_file():
            if hashlib.sha256(pending.read_bytes()).hexdigest() != output_sha:
                raise HTTPException(409, "Candidate staging revision mismatch")
            pending.replace(target)
        else:
            if not storage_capacity(len(raw))["ready"]:
                raise HTTPException(507, "Candidate review storage exhausted")
            target.parent.mkdir(parents=True, exist_ok=True)
            with pending.open("xb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            pending.replace(target)
    if not isinstance(old, dict):
        job.payload_json = {**payload, "review_candidate": {
            "status": "review_candidate",
            "method": CANDIDATE_METHOD,
            "source_sha256": source_sha,
            "output_sha256": output_sha,
            "bytes": len(raw),
            "qa": qa,
            "manual_qa_required": True,
            "rights_status": "unverified",
            "print_ready": False,
            "created_at": utcnow().isoformat(),
        }}
        agent.last_seen_at = utcnow()
        db.commit()
    return {"ok": True, "job_id": job.id, "candidate_ready": True,
            "output_sha256": output_sha, "status": "review_candidate",
            "original_result_status": "needs_ai",
            "print_ready": False, "rights_status": "unverified"}


@image_router.get("/candidates/{job_id}")
def read_review_candidate(
    job_id: str, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    tenant = principal.active_tenant_id
    job = db.get(ProcessingJobModel, job_id)
    if (job is None or job.tenant_id != tenant or
        job.job_type != EXTRACTION_JOB_TYPE or job.cancellation_requested or
        db.get(EtsySpyPicked, (tenant, job.entity_id)) is None):
        raise HTTPException(404, "Review candidate not found")
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    original = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    review = payload.get("review_candidate")
    if (original.get("status") != "needs_ai" or
        not isinstance(review, dict) or
        review.get("status") != "review_candidate"):
        raise HTTPException(404, "Review candidate not available")
    file = _storage_path(tenant, job.id).with_suffix(".candidate.png")
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != review.get("output_sha256"):
        raise HTTPException(404, "Review candidate asset missing or changed")
    return FileResponse(str(file), media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": 'inline; filename="cam-design-candidate-review.png"'})



def _design_media(job: ProcessingJobModel, asset_id: str) -> tuple[Path, str]:
    """Resolve a mapped or historical image only from authenticated job data."""
    from .design_artifacts import project_design_assets
    if asset_id not in {"fast", "candidate"} and not re.fullmatch(r"[0-9a-f]{24}", asset_id):
        raise HTTPException(404, "Design not found")
    match = next((item for item in project_design_assets(job) if item["id"] == asset_id), None)
    if match is None:
        raise HTTPException(404, "Design not found")
    original = _storage_path(job.tenant_id, job.id)
    if asset_id == "fast":
        return original, match["output_sha256"]
    if asset_id == "candidate":
        return original.with_suffix(".candidate.png"), match["output_sha256"]
    return original.with_name(f"{job.id}.design.{asset_id}.png"), match["output_sha256"]


def _checked_design_media(
    job_id: str, asset_id: str, db: Session, tenant: str,
) -> tuple[Path, str]:
    job = db.get(ProcessingJobModel, job_id)
    if (job is None or job.tenant_id != tenant or job.job_type != EXTRACTION_JOB_TYPE
        or job.cancellation_requested or
        db.get(EtsySpyPicked, (tenant, job.entity_id)) is None):
        raise HTTPException(404, "Design not found")
    source, digest = _design_media(job, asset_id)
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise HTTPException(404, "Design asset missing or changed")
    return source, digest


@image_router.post("/{agent_id}/designs")
async def upload_design_mapping(
    agent_id: str, request: Request, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Map up to 12 immutable, position-specific PNGs to one completed Picked job.

    A thumbnail is generated on demand; positions may only be set from genuine
    source evidence, not guessed from the garment's visible front mockup.
    """
    from .design_artifacts import (
        DESIGN_POSITIONS, DESIGN_METHODS, MAX_DESIGNS_PER_JOB, design_asset_id,
    )
    agent = _authenticate(db, agent_id, authorization)
    _enabled()
    data = await _bounded_json(request, MAX_RECEIPT_BYTES)
    job_id = str(data.get("job_id") or "")
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
    ).with_for_update())
    if job is None or job.tenant_id != agent.tenant_id or job.job_type != EXTRACTION_JOB_TYPE:
        raise HTTPException(404, "Extraction job not found")
    if (job.status != "completed" or job.cancellation_requested or
        job.claimed_by != "etsy-image:" + agent.id or
        db.get(EtsySpyPicked, (agent.tenant_id, job.entity_id)) is None):
        raise HTTPException(409, "Job is not available for position mapping")
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("status") not in {"needs_review", "needs_ai"}:
        raise HTTPException(409, "Only completed extraction jobs accept design mappings")
    source_sha = str(data.get("source_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha) or source_sha != result.get("source_sha256"):
        raise HTTPException(422, "Design source hash mismatch")
    position, method, variant = data.get("position"), data.get("method"), data.get("variant")
    if position not in DESIGN_POSITIONS or method not in DESIGN_METHODS:
        raise HTTPException(422, "Unsupported position or extraction method")
    if type(variant) is not int or not 1 <= variant <= 12:
        raise HTTPException(422, "Variant must be 1..12")
    if (position != "unknown" and data.get("position_verified") is not True):
        raise HTTPException(422, "Design position must be verified against source evidence")
    if any(data.get(key) is not False for key in ("print_ready", "rights_verified", "ocr_verified")):
        raise HTTPException(422, "Design requires print, OCR and rights review")
    digest = str(data.get("output_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(422, "Invalid design SHA256")
    raw_b64 = data.get("png_base64")
    if not isinstance(raw_b64, str) or len(raw_b64) > MAX_RECEIPT_BYTES:
        raise HTTPException(422, "Design PNG required")
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Invalid design base64") from None
    _verify_png(raw)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise HTTPException(422, "Design PNG hash mismatch")
    assets = payload.get("design_assets") if isinstance(payload.get("design_assets"), list) else []
    for current in assets:
        if current.get("position") == position and current.get("variant") == variant:
            if current.get("output_sha256") != digest or current.get("method") != method:
                raise HTTPException(409, "Position/variant already has another immutable PNG")
            return {"ok": True, "job_id": job.id, "design_id": current["id"],
                    "status": "needs_review", "already_mapped": True}
    if len(assets) >= MAX_DESIGNS_PER_JOB:
        raise HTTPException(409, "Maximum design versions reached")
    asset_id = design_asset_id(job.id, position, variant, digest)
    original = _storage_path(agent.tenant_id, job.id)
    target = original.with_name(f"{job.id}.design.{asset_id}.png")
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise HTTPException(409, "Design asset path hash mismatch")
    else:
        if not storage_capacity(len(raw))["ready"]:
            raise HTTPException(507, "Design storage unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix(".pending")
        if pending.is_file():
            if hashlib.sha256(pending.read_bytes()).hexdigest() == digest:
                pending.replace(target)
            else:
                pending.unlink()
        if not target.is_file():
            with pending.open("xb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            pending.replace(target)
    entry = {
        "id": asset_id, "position": position, "variant": variant,
        "method": method, "output_sha256": digest,
        "source_sha256": source_sha, "position_verified": position != "unknown",
        "status": "needs_review", "created_at": utcnow().isoformat(),
        "print_ready": False, "rights_status": "unverified",
    }
    job.payload_json = {**payload, "design_assets": [*assets, entry]}
    agent.last_seen_at = utcnow()
    db.commit()
    return {"ok": True, "job_id": job.id, "design_id": asset_id,
            "status": "needs_review", "already_mapped": False,
            "print_ready": False, "rights_status": "unverified"}


@image_router.get("/designs/{job_id}/{asset_id}/original")
def view_design_original(
    job_id: str, asset_id: str, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    source, _ = _checked_design_media(job_id, asset_id, db, principal.active_tenant_id)
    return FileResponse(str(source), media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": 'inline; filename="design-review.png"'})


@image_router.get("/designs/{job_id}/{asset_id}/thumbnail")
def view_design_thumbnail(
    job_id: str, asset_id: str, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    import io
    from PIL import Image, UnidentifiedImageError
    source, _ = _checked_design_media(job_id, asset_id, db, principal.active_tenant_id)
    thumb = source.with_name(source.name[:-4] + ".thumb.png")
    if not thumb.is_file():
        try:
            with Image.open(source) as img:
                img.load()
                img = img.convert("RGBA")
                img.thumbnail((192, 192), Image.Resampling.LANCZOS)
                pending = thumb.with_suffix(".pending")
                with pending.open("wb") as file:
                    img.save(file, "PNG", optimize=True)
                    file.flush()
                    os.fsync(file.fileno())
                pending.replace(thumb)
        except (OSError, ValueError, UnidentifiedImageError):
            raise HTTPException(404, "Design preview unavailable") from None
    return FileResponse(str(thumb), media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff"})


@image_router.get("/assets/{job_id}")
def review_image_asset(
    job_id: str, principal: CurrentPrincipal = Depends(READ),
    db: Session = Depends(get_db),
):
    job = db.get(ProcessingJobModel, job_id)
    if (job is None or job.tenant_id != principal.active_tenant_id or
        job.job_type != EXTRACTION_JOB_TYPE):
        raise HTTPException(404, "Image not found")
    result = (job.payload_json or {}).get("result") or {}
    if not isinstance(result, dict) or result.get("status") != "needs_review":
        raise HTTPException(404, "Review image not available")
    file = _storage_path(principal.active_tenant_id, job_id)
    if not file.is_file():
        raise HTTPException(404, "Review image missing from storage")
    return FileResponse(str(file), media_type="image/png",
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": 'inline; filename="cam-design-review.png"'})
