"""Idempotent acknowledgement of a previously committed video completion.

A replay grants no lease, changes no state, and cannot approve a new receipt.
Only the original worker and exact success request can replay it for 24 hours.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

POLICY = "CAM_MUSE_RECEIPT_ACK_REPLAY_V1"
KEY = "completionAckReplay"


def request_hash(payload):
    return hashlib.sha256(json.dumps(
        payload.model_dump(mode="json"), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def receipt_hashes(job, required, verified):
    if job is None or not required or not set(required) <= verified(job):
        return None
    uploads = (job.output_receipts or {}).get("drive_uploads") or {}
    return {r: uploads[r]["sha256"] for r in required}


def record(cmd, job, payload, worker_id, required, verified, now):
    if (cmd.phase != "create_video" or cmd.status != "receipt_submitted"
            or payload.outcome != "success" or job is None
            or cmd.artwork_sha256 != job.artwork_sha256):
        return None
    hashes = receipt_hashes(job, required, verified)
    if hashes is None:
        return None
    return {"policy": POLICY, "requestSha256": request_hash(payload),
            "workerId": worker_id, "artworkSha256": job.artwork_sha256,
            "requiredRatios": list(required), "receiptSha256ByRatio": hashes,
            "expiresAtUtc": (now + timedelta(hours=24)).isoformat()}


def matches(cmd, job, payload, worker_id, verified, now):
    if (cmd is None or cmd.status != "receipt_submitted" or cmd.phase != "create_video"
            or cmd.lease_hash is not None or cmd.lease_expires_at is not None
            or payload.outcome != "success" or job is None
            or cmd.artwork_sha256 != job.artwork_sha256):
        return False
    m = (cmd.details or {}).get(KEY)
    if (not isinstance(m, dict) or m.get("policy") != POLICY
            or m.get("workerId") != worker_id
            or m.get("artworkSha256") != job.artwork_sha256
            or not isinstance(m.get("requestSha256"), str)):
        return False
    try:
        expiry = datetime.fromisoformat(m["expiresAtUtc"])
        if expiry.tzinfo is None or now >= expiry:
            return False
    except (ValueError, TypeError, KeyError):
        return False
    required = m.get("requiredRatios")
    if required not in (["9:16"], ["16:9"], ["9:16", "16:9"], ["16:9", "9:16"]):
        return False
    hashes = receipt_hashes(job, required, verified)
    return bool(hashes is not None and hashes == m.get("receiptSha256ByRatio")
                and hmac.compare_digest(m["requestSha256"], request_hash(payload)))
