"""Reconcile worker-reported, pre-claim listing holds with CAM discovery.

No renderer call, new job, attempt reset, media deletion, lease transfer,
or authority to override a provider refusal. Holds use the existing receipt
and command audit records, bound to the current artwork SHA.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from .model import CamMuseCommand, CamMuseJob

POLICY = "CAM_MUSE_SKIP_RECONCILIATION_V1"
KEY = "museSkipReconciliation"
REASONS = ("terminal_hold", "operator_paused", "source_blocked", "temporary_blocked", "provider_refused")
RELEASABLE = {"operator_paused", "source_blocked", "temporary_blocked"}


class SkipReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reportId: UUID
    listingId: str = Field(pattern=r"^[0-9]{5,20}$")
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observedAtUtc: datetime
    reason: Literal["terminal_hold", "operator_paused", "source_blocked", "temporary_blocked", "provider_refused"]
    holdSeconds: int | None = Field(default=None, ge=1800, le=43200)

    @model_validator(mode="after")
    def check_report(self):
        if self.observedAtUtc.tzinfo is None:
            raise ValueError("Timezone required")
        if (self.reason == "temporary_blocked") != (self.holdSeconds is not None):
            raise ValueError("Only temporary_blocked requires holdSeconds")
        return self


class SkipRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reportId: UUID
    listingId: str = Field(pattern=r"^[0-9]{5,20}$")
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def contract():
    return {
        "policy": POLICY,
        "reportPathTemplate": "/api/v1/muse-bridge/commands/{commandId}/skip-report",
        "reporter": "muse-01",
        "reasonValues": list(REASONS),
        "temporaryHoldSecondsBounds": [1800, 43200],
        "providerCooldownPath": "/api/v1/muse-bridge/workers/cooldown",
        "requiresCurrentCommandAndArtworkSha": True,
        "requiresPreclaimPendingCommand": True,
        "preservesLeaseAttemptsAndMedia": True,
        "persistentHoldRequiresOperatorReview": True,
        "instructions": (
            "Report actual approved local listing skips only, before claiming. "
            "Never infer a hold from an old failure alone. Use a stable reportId "
            "and actual observation time on retry; do not renew from polling. "
            "For a real account-wide 429 use workers/cooldown, not a listing hold. "
            "Never retry a provider refusal or remove a safety/QA hold. "
            "After CAM acknowledges an active hold, refresh discovery; CAM will "
            "exclude it from eligible queue capacity. Existing live leases may finish."
        ),
    }


def utc(value):
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def marker(job):
    r = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    record = r.get(KEY)
    if (not isinstance(record, dict) or record.get("policy") != POLICY
            or record.get("artworkSha256") != job.artwork_sha256
            or record.get("reason") not in REASONS):
        return None
    return record


def active(job, now=None):
    record = marker(job)
    if not record or record.get("releasedAtUtc"):
        return False
    if record["reason"] != "temporary_blocked":
        return True
    try:
        until = datetime.fromisoformat(record["untilUtc"])
        if until.tzinfo is None:
            return True
        return utc(until) > utc(now or datetime.now(timezone.utc))
    except (ValueError, KeyError, TypeError):
        # Malformed durable hold needs operator review, not a silent release.
        return True


def locked_pair(db, tenant, command_id, payload):
    cmd = db.scalar(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.command_id == command_id,
    ).with_for_update())
    if (cmd is None or cmd.listing_key != payload.listingId
            or cmd.artwork_sha256 != payload.artworkSha256
            or cmd.action != "create_video" or cmd.phase != "create_video"):
        raise HTTPException(409, "Matching video command and Native Master required")
    job = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.listing_key == payload.listingId,
    ).with_for_update())
    if job is None or job.artwork_sha256 != payload.artworkSha256:
        raise HTTPException(409, "Listing or Native Master changed")
    return cmd, job


def require_unclaimed(db, tenant, cmd, job):
    if (cmd.status != "pending" or cmd.lease_hash is not None
            or cmd.lease_expires_at is not None or job.status != "pending"
            or job.lease_hash is not None or job.lease_expires_at is not None):
        raise HTTPException(409, "Skip reconciliation cannot modify a claimed or completed lease")
    # Even malformed/expired competing leases require ordinary reconciliation.
    other = db.scalar(select(CamMuseCommand.command_id).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.listing_key == job.listing_key,
        CamMuseCommand.status == "claimed",
    ).limit(1))
    if other is not None:
        raise HTTPException(409, "Listing has a claimed command; retain its lease")


def response(cmd, job, record, now, reused):
    return {"ok": True, "policy": POLICY, "commandId": cmd.command_id,
            "listingId": job.listing_key, "reportId": record["reportId"],
            "reason": record["reason"], "holdActive": active(job, now),
            "untilUtc": record.get("untilUtc"), "reused": reused,
            "commandStatus": cmd.status, "attemptsPreserved": True,
            "priorMediaPreserved": True, "liveLeaseTransferred": False}


def report(db, tenant, command_id, payload, worker, eligible, now=None):
    now = utc(now or datetime.now(timezone.utc))
    cmd, job = locked_pair(db, tenant, command_id, payload)
    previous = marker(job)
    cmd_details = dict(cmd.details or {})
    old = cmd_details.get(KEY)
    rid = str(payload.reportId)
    observed = utc(payload.observedAtUtc).isoformat()
    # Same report is acknowledged without extending or reactivating a hold.
    if isinstance(old, dict) and old.get("reportId") == rid:
        if (old.get("reason") != payload.reason or old.get("holdSeconds") != payload.holdSeconds
                or old.get("observedAtUtc") != observed or old.get("workerId") != worker):
            raise HTTPException(409, "Skip report ID conflict")
        if previous and previous.get("reportId") == rid:
            return response(cmd, job, previous, now, True)
        return {**response(cmd, job, old, now, True), "holdActive": False, "superseded": True}
    if previous and active(job, now):
        raise HTTPException(409, "Listing already on reconciled hold; do not renew or override")
    if utc(payload.observedAtUtc) < now - timedelta(minutes=15) or utc(payload.observedAtUtc) > now + timedelta(minutes=2):
        raise HTTPException(422, "Skip observation timestamp stale or in future")
    # Old observations cannot restore a released/expired marker.
    if previous and observed <= previous.get("observedAtUtc", ""):
        raise HTTPException(409, "New skip report requires a fresh observation")
    require_unclaimed(db, tenant, cmd, job)
    if cmd.attempts >= 5 or job.attempts >= 5 or not eligible(db, tenant, job, cmd) == "create_video":
        raise HTTPException(409, "Command no longer eligible; preserve existing CAM gate")
    record = {"policy": POLICY, "reportId": rid, "commandId": command_id,
              "artworkSha256": job.artwork_sha256, "workerId": worker,
              "reason": payload.reason, "holdSeconds": payload.holdSeconds,
              "observedAtUtc": observed, "recordedAtUtc": now.isoformat(),
              "untilUtc": (now + timedelta(seconds=payload.holdSeconds)).isoformat() if payload.holdSeconds else None,
              "origin": "worker_reported_local_hold"}
    job.output_receipts = {**(job.output_receipts or {}), KEY: record}
    cmd.details = {**cmd_details, KEY: record}
    # Keep the existing pending command and its request identity. No extra
    # retry history or failed render is manufactured by this operation.
    db.commit()
    return response(cmd, job, record, now, False)


def release(db, tenant, command_id, payload, now=None):
    now = utc(now or datetime.now(timezone.utc))
    cmd, job = locked_pair(db, tenant, command_id, payload)
    record = marker(job)
    if not record or record.get("reportId") != str(payload.reportId) or record.get("commandId") != command_id:
        raise HTTPException(409, "Hold identity changed")
    if record["reason"] not in RELEASABLE:
        raise HTTPException(409, "Terminal or provider refusal hold requires separate review; not releasable here")
    if record.get("releasedAtUtc"):
        return response(cmd, job, record, now, True)
    require_unclaimed(db, tenant, cmd, job)
    record = {**record, "releasedAtUtc": now.isoformat(), "releasedBy": "operator"}
    job.output_receipts = {**(job.output_receipts or {}), KEY: record}
    cmd.details = {**(cmd.details or {}), KEY: record}
    db.commit()
    return response(cmd, job, record, now, False)


def summary(db, tenant, now=None):
    now = now or datetime.now(timezone.utc)
    rows = db.scalars(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.output_receipts[KEY].as_string().is_not(None),
    )).all()
    counts = {}
    items = []
    for job in rows:
        if not active(job, now):
            continue
        r = marker(job)
        counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        if len(items) < 50:
            items.append({"listingId": job.listing_key, **{
                k: r.get(k) for k in ("commandId", "reason", "workerId", "reportId", "untilUtc", "recordedAtUtc")
            }})
    return {"policy": POLICY, "activeListings": sum(counts.values()), "byReason": counts,
            "items": items, "itemsTruncated": sum(counts.values()) > len(items)}
