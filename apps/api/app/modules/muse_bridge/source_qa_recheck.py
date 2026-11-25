"""Narrow operator-approved re-review of a mis-scoped historical artwork QA.

Never creates a PASS, resets an attempt, clears a real hold, or queues video.
The original verdict is archived only when a normal review slot is allocated.
"""
from __future__ import annotations

import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field
from fastapi import HTTPException
from sqlalchemy import select

from .model import CamMuseJob, CamMuseCommand
from . import native_placement_review as placement, workflow_contract as workflow

POLICY = "CAM_MUSE_SOURCE_QA_RECHECK_V1"
KEY = "sourceQaScopeRecheck"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artworkSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    priorReviewSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    priorCommandId: str = Field(pattern=r"^[a-f0-9-]{36}$")
    reason: str = Field(min_length=15, max_length=240)


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def marker(job):
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    value = receipts.get(KEY)
    return value if isinstance(value, dict) and value.get("policy") == POLICY else None


def pending(job):
    m = marker(job)
    return bool(m and m.get("state") == "approved_pending_slot")


def claim_limit(command):
    details = getattr(command, "details", None) or {}
    scope = details.get(KEY)
    if isinstance(scope, dict) and scope.get("policy") == POLICY:
        n = scope.get("priorReviewAttempts")
        if type(n) is int and 1 <= n < 5:
            return 5 - n
        return 0
    return 5


def check_candidate(job, history, native):
    """Conservative historical scope error, not a new approval or pass."""
    if (job is None or job.status != "qa_hold" or job.source_state != "qa_hold"
            or job.attempts != 0 or job.lease_hash is not None
            or job.lease_expires_at is not None or not job.product_image_url
            or job.artwork_asset_path or native is None):
        return False
    receipts = job.output_receipts
    if not isinstance(receipts, dict) or set(receipts) - {"visual_qa", KEY}:
        return False
    qa = receipts.get("visual_qa")
    if not isinstance(qa, dict):
        return False
    notes = qa.get("notes")
    checks = qa.get("checks")
    confidence = qa.get("confidence")
    if (qa.get("verdict") != "fail" or qa.get("reviewer") != "muse"
            or qa.get("review_scope_policy") == workflow.QA_SCOPE
            or qa.get("artwork_sha256") != job.artwork_sha256
            or type(confidence) not in (int, float) or not 0.95 <= confidence <= 1
            or not isinstance(checks, dict)
            or not all(checks.get(k) is True for k in workflow.CHECKS)
            or not isinstance(notes, str)
            or not notes.startswith("GARMENT_MISMATCH_HOLD:") or "C1717" not in notes):
        return False
    # Only a single failed comparative review, never a prior video attempt,
    # provider refusal, operator hold, or other execution history.
    if len(history) != 1:
        return False
    prior = history[0]
    d = prior.details or {}
    if (prior.status != "failed" or prior.phase != "review_artwork"
            or prior.action not in ("review_artwork", "create_video")
            or prior.artwork_sha256 != job.artwork_sha256
            or prior.attempts != 1 or prior.lease_hash is not None
            or prior.lease_expires_at is not None
            or "GARMENT_MISMATCH_HOLD" not in str(d.get("notes", ""))
            or any(d.get(k) for k in ("softHold", "videoCapabilityHold",
                                    "renderRetry", "lastRendererTransientFailure", "lastCooldown"))):
        return False
    deny = ("provider_refused", "provider refusal", "provider refused",
            "rights denied", "permission denied", "manual hold", "operator hold",
            "do not retry", "renderer_capability_blocked", "print_surface_qa_hold",
            "fabric_surface_tracking_blocked", "safety refusal")
    if any(x in (notes + " " + str(d.get("notes", ""))).lower() for x in deny):
        return False
    pr = qa.get("placement_review")
    if native.position == "unknown":
        if not isinstance(pr, dict):
            return False
        if (pr.get("policy") != placement.POLICY
                or pr.get("observedPlacement") != "front_center"
                or pr.get("placementReviewId") != placement.review_id(
                    native, job.artwork_sha256, job.product_image_url)
                or pr.get("jobId") != native.job_id or pr.get("assetId") != native.asset_id):
            return False
    elif native.position != "front" or (
            isinstance(pr, dict) and pr.get("observedPlacement") != "front_center"):
        return False
    return True


def approve(db, tenant, listing_id, payload, now, native_lookup):
    job = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.listing_key == listing_id,
    ).with_for_update())
    if job is None:
        raise HTTPException(404, "Listing not found")
    old = marker(job)
    if old:
        if (old.get("priorReviewSha256") == payload.priorReviewSha256
                and old.get("priorCommandId") == payload.priorCommandId
                and old.get("artworkSha256") == payload.artworkSha256):
            return {"ok": True, "policy": POLICY, "listingId": listing_id,
                    "state": old["state"], "reused": True, "videoApproved": False}
        raise HTTPException(409, "Re-review already requested; no repeat authorization")
    history = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.listing_key == listing_id,
    ).limit(2)).all()
    native = native_lookup(db, tenant, job)
    if (not check_candidate(job, history, native)
            or job.artwork_sha256 != payload.artworkSha256
            or history[0].command_id != payload.priorCommandId
            or fingerprint(job.output_receipts["visual_qa"]) != payload.priorReviewSha256):
        raise HTTPException(409, "Historical source-scope review not eligible or evidence changed")
    approval = {"policy": POLICY, "state": "approved_pending_slot",
                "artworkSha256": job.artwork_sha256,
                "priorReviewSha256": payload.priorReviewSha256,
                "priorCommandId": payload.priorCommandId,
                "sourceImageSha256": fingerprint(job.product_image_url),
                "nativeJobId": native.job_id, "nativeAssetId": native.asset_id,
                "nativePosition": native.position, "priorReviewAttempts": history[0].attempts,
                "operatorReason": payload.reason, "approvedAtUtc": now.isoformat()}
    job.output_receipts = {**job.output_receipts, KEY: approval}
    job.updated_at = now
    db.commit()
    return {"ok": True, "policy": POLICY, "listingId": listing_id,
            "state": approval["state"], "reused": False, "videoApproved": False}


def prepared_receipts(job, history, native, now, command_id):
    m = marker(job)
    if not pending(job) or not check_candidate(job, history, native):
        return None
    qa = job.output_receipts["visual_qa"]
    if (m.get("artworkSha256") != job.artwork_sha256
            or m.get("priorReviewSha256") != fingerprint(qa)
            or m.get("priorCommandId") != history[0].command_id
            or m.get("sourceImageSha256") != fingerprint(job.product_image_url)
            or m.get("nativeJobId") != native.job_id or m.get("nativeAssetId") != native.asset_id
            or m.get("nativePosition") != native.position
            or m.get("priorReviewAttempts") != history[0].attempts):
        return None
    # Archive immutable prior fail and retain qa_hold. Fresh real review is
    # required before normal video eligibility. This is never a verdict.
    return {KEY: {**m, "state": "queued", "priorReview": qa,
                  "reviewCommandId": command_id, "queuedAtUtc": now.isoformat()}}


def verdict_marker(receipts, verdict, now):
    m = receipts.get(KEY)
    if isinstance(m, dict) and m.get("policy") == POLICY and m.get("state") == "queued":
        return {**receipts, KEY: {**m, "state": "reviewed", "newVerdict": verdict,
                                  "reviewedAtUtc": now.isoformat()}}
    return receipts
