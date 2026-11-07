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
import os
import re
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
VN_TIMEZONE = timezone(timedelta(hours=7))


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
    }


def transport_enabled() -> bool:
    return extraction_enabled()


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
    return extraction_overview(db, principal.active_tenant_id)


@image_router.post("/{agent_id}/claim")
def claim_image_job(
    agent_id: str, authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    agent = _authenticate(db, agent_id, authorization)
    _enabled()
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
        return _task(active, agent)
    # Conservative per-tenant cap for the requested ~200/day workload.
    # Existing leased work can still renew through /check-job.
    if claimed_today(db, agent.tenant_id) >= daily_cap():
        agent.last_seen_at = now
        db.commit()
        return {"task": None, "pause_reason": "daily_cap_reached"}
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
    job = db.get(ProcessingJobModel, job_id)
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
    job = db.get(ProcessingJobModel, job_id)
    if (job is None or job.tenant_id != agent.tenant_id or
        job.job_type != EXTRACTION_JOB_TYPE):
        raise HTTPException(404, "Image extraction job not found")
    status = data.get("status")
    if status not in {"needs_review", "needs_ai", "failed"}:
        raise HTTPException(422, "Invalid extraction status")
    engine = data.get("engine")
    if engine not in {"fast_cpu", "ai_review"}:
        raise HTTPException(422, "Invalid extraction engine")
    if status == "needs_review" and engine != "fast_cpu":
        raise HTTPException(422, "Only Fast Extraction may submit a review PNG")
    if status == "needs_ai" and engine != "ai_review":
        raise HTTPException(422, "AI fallback must be routed to manual review")
    lease = str(data.get("lease_token") or "")
    if not hmac.compare_digest(lease, _lease_token(agent, job)):
        raise HTTPException(403, "Stale or invalid agent lease")
    source_sha256 = str(data.get("source_sha256") or "")
    if source_sha256 and not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise HTTPException(422, "Invalid source hash")
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
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != output_hash:
                raise HTTPException(409, "Immutable PNG revision mismatch")
        else:
            temp = target.with_suffix(".pending")
            with temp.open("xb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            temp.replace(target)
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
    job.payload_json = {**(job.payload_json or {}), "result": result}
    job.status = "completed"
    job.completed_at = utcnow()
    job.lease_expires_at = None
    agent.last_seen_at = utcnow()
    db.commit()
    return {"ok": True, "receipt_id": receipt_id, "output_sha256": output_hash}


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
