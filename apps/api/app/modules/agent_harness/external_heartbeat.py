"""Server-attested external executor heartbeat for an already-owned CAM job.

This is intentionally *not* a dispatcher, slot reservation or job lease
renewer. The producer is the dedicated paired Image Agent credential; only a
server-only signing key can seal the bounded evidence stored on ProcessingJob.
The existing ProcessingJob claim/attempt/lease remains authoritative.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from app.modules.processing.model import ProcessingJobModel

_EVIDENCE_FIELD = "_cam_agent_external_heartbeat_v1"
_PROTOCOL = "cam-agent-external-hb-v1"
_MAX_TTL_SECONDS = 45
_MIN_LEASE_SECONDS = 5
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")


class ExternalHeartbeatRejected(ValueError):
    """Expected job/attempt/lease fence violation; never reveal owner IDs."""


def external_heartbeat_enabled() -> bool:
    return os.getenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED", "0") == "1"


def external_heartbeat_signing_key() -> bytes | None:
    """Independent server-only 256-bit key. No fallback to device auth hashes."""
    raw = os.getenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY", "")
    return bytes.fromhex(raw) if _HEX_256.fullmatch(raw) else None


def _utc(timestamp: datetime | None) -> datetime | None:
    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _signature(key: bytes, evidence: dict[str, Any]) -> str:
    encoded = json.dumps(
        evidence, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return hmac.new(key, b"cam-agent-ext-hb-v1:" + encoded, hashlib.sha256).hexdigest()


def issue_external_heartbeat(
    job: ProcessingJobModel, *, agent_id: str, expected_attempt: int,
    signing_key: bytes, now: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded, server-signed record only for an active owned lease."""
    clock = _utc(now) if now is not None else datetime.now(timezone.utc)
    if clock is None or len(signing_key) != 32:
        raise ExternalHeartbeatRejected("heartbeat_configuration_invalid")
    if (
        job.job_type != "etsy_design_extract"
        or job.status != "processing"
        or job.claimed_by != "etsy-image:" + agent_id
        or job.cancellation_requested
    ):
        raise ExternalHeartbeatRejected("job_not_owned_or_cancelled")
    if (
        type(expected_attempt) is not int
        or type(job.attempt_count) is not int
        or type(job.max_attempts) is not int
        or not 1 <= expected_attempt == job.attempt_count <= job.max_attempts
    ):
        raise ExternalHeartbeatRejected("job_attempt_changed")
    claim_at = _utc(job.claimed_at)
    lease_end = _utc(job.lease_expires_at)
    if (
        claim_at is None or lease_end is None
        or claim_at > clock
        or lease_end <= clock
        or lease_end <= claim_at
    ):
        raise ExternalHeartbeatRejected("job_lease_not_active")
    issued = int(clock.timestamp())
    expires = min(issued + _MAX_TTL_SECONDS, int(lease_end.timestamp()))
    if expires - issued < _MIN_LEASE_SECONDS:
        raise ExternalHeartbeatRejected("job_lease_near_expiry")
    evidence = {
        "protocol": _PROTOCOL,
        "tenant_id": job.tenant_id,
        "agent_id": agent_id,
        "job_id": job.id,
        "attempt_count": job.attempt_count,
        "claim_epoch": int(claim_at.timestamp()),
        "issued_epoch": issued,
        "expires_epoch": expires,
        "nonce": secrets.token_hex(12),
    }
    return {**evidence, "signature": _signature(signing_key, evidence)}


def observe_external_heartbeat(
    job: ProcessingJobModel, *, signing_key: bytes | None,
    agent_active: bool, now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only, fail-closed verification; no bearer or worker ID is exposed."""
    result = {
        "state": "unverified",
        "worker_heartbeat_authenticated": False,
        "executor_slot_reserved": False,
        "dispatch_allowed": False,
        "execution_enabled": False,
        "heartbeat_age_seconds": None,
        "heartbeat_expires_in_seconds": 0,
    }
    clock = _utc(now) if now is not None else datetime.now(timezone.utc)
    if clock is None or signing_key is None or len(signing_key) != 32:
        return {**result, "state": "signing_key_unavailable"}
    if not agent_active or job.job_type != "etsy_design_extract":
        return {**result, "state": "device_unverified"}
    if job.status != "processing" or job.cancellation_requested:
        return {**result, "state": "job_not_processing"}
    lease_end = _utc(job.lease_expires_at)
    claimed_at = _utc(job.claimed_at)
    if lease_end is None or lease_end <= clock or claimed_at is None:
        return {**result, "state": "lease_expired"}
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    raw = payload.get(_EVIDENCE_FIELD)
    if not isinstance(raw, dict) or set(raw) != {
        "protocol", "tenant_id", "agent_id", "job_id", "attempt_count",
        "claim_epoch", "issued_epoch", "expires_epoch", "nonce", "signature",
    }:
        return {**result, "state": "heartbeat_missing"}
    signature = raw.get("signature")
    unsigned = {k: v for k, v in raw.items() if k != "signature"}
    if (
        not isinstance(signature, str)
        or _HEX_256.fullmatch(signature) is None
        or not hmac.compare_digest(signature, _signature(signing_key, unsigned))
    ):
        return {**result, "state": "signature_invalid"}
    if (
        raw["protocol"] != _PROTOCOL
        or raw["tenant_id"] != job.tenant_id
        or raw["job_id"] != job.id
        or job.claimed_by != "etsy-image:" + str(raw["agent_id"])
        or type(raw["attempt_count"]) is not int
        or raw["attempt_count"] != job.attempt_count
        or type(raw["claim_epoch"]) is not int
        or raw["claim_epoch"] != int(claimed_at.timestamp())
        or type(raw["issued_epoch"]) is not int
        or type(raw["expires_epoch"]) is not int
        or not isinstance(raw["nonce"], str)
        or re.fullmatch(r"[0-9a-f]{24}", raw["nonce"]) is None
    ):
        return {**result, "state": "job_binding_changed"}
    issued = raw["issued_epoch"]
    expires = raw["expires_epoch"]
    now_epoch = int(clock.timestamp())
    if (
        issued > now_epoch + 2 or now_epoch - issued > _MAX_TTL_SECONDS
        or expires <= now_epoch or expires - issued > _MAX_TTL_SECONDS
        or expires <= issued or expires > int(lease_end.timestamp())
        or issued < int(claimed_at.timestamp())
    ):
        return {**result, "state": "heartbeat_expired"}
    return {
        **result,
        "state": "authenticated_heartbeat_observed",
        "worker_heartbeat_authenticated": True,
        "heartbeat_age_seconds": max(0, now_epoch - issued),
        "heartbeat_expires_in_seconds": max(0, expires - now_epoch),
    }


def inspect_completed_heartbeat(
    job: ProcessingJobModel, *, signing_key: bytes | None,
    agent_registered: bool, now: datetime | None = None,
) -> dict[str, Any]:
    """Retrospectively verify *one* signed lease observation on a completed job.

    A completed Image Agent result clears lease_expires_at. Its proof must be
    checked against the immutable attempt/claim and completion boundary, not
    mistaken for fresh executor readiness. No historical record can authorize
    capacity, dispatch or provider output quality.
    """
    result: dict[str, Any] = {
        "state": "historical_unverified",
        "historical_heartbeat_verified": False,
        "historical_receipt_age_seconds": None,
        "worker_heartbeat_authenticated": False,
        "executor_slot_reserved": False,
        "dispatch_allowed": False,
        "execution_enabled": False,
    }
    clock = _utc(now) if now is not None else datetime.now(timezone.utc)
    if clock is None or signing_key is None or len(signing_key) != 32:
        result["state"] = "signing_key_unavailable"
        return result
    if not agent_registered or job.job_type != "etsy_design_extract":
        result["state"] = "device_unverified"
        return result
    if job.status != "completed":
        result["state"] = "job_not_completed"
        return result
    claimed_at, completed_at = _utc(job.claimed_at), _utc(job.completed_at)
    if (
        claimed_at is None or completed_at is None
        or completed_at < claimed_at or completed_at > clock + timedelta(seconds=5)
    ):
        result["state"] = "completion_boundary_invalid"
        return result
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    outcome = payload.get("result")
    if (
        not isinstance(outcome, dict)
        or outcome.get("status") not in {"needs_ai", "needs_review", "failed"}
        or not isinstance(outcome.get("receipt_id"), str)
        or re.fullmatch(r"[0-9a-f]{40}", outcome["receipt_id"]) is None
    ):
        result["state"] = "completed_result_missing"
        return result
    record = payload.get(_EVIDENCE_FIELD)
    if not isinstance(record, dict) or set(record) != {
        "protocol", "tenant_id", "agent_id", "job_id", "attempt_count",
        "claim_epoch", "issued_epoch", "expires_epoch", "nonce", "signature",
    }:
        result["state"] = "heartbeat_missing"
        return result
    signature = record.get("signature")
    unsigned = {key: value for key, value in record.items() if key != "signature"}
    if (
        not isinstance(signature, str)
        or _HEX_256.fullmatch(signature) is None
        or not hmac.compare_digest(signature, _signature(signing_key, unsigned))
    ):
        result["state"] = "signature_invalid"
        return result
    if (
        record["protocol"] != _PROTOCOL
        or record["tenant_id"] != job.tenant_id
        or record["job_id"] != job.id
        or not isinstance(record["agent_id"], str)
        or job.claimed_by != "etsy-image:" + record["agent_id"]
        or type(record["attempt_count"]) is not int
        or type(job.attempt_count) is not int
        or record["attempt_count"] != job.attempt_count
        or type(record["claim_epoch"]) is not int
        or record["claim_epoch"] != int(claimed_at.timestamp())
        or type(record["issued_epoch"]) is not int
        or type(record["expires_epoch"]) is not int
        or not isinstance(record["nonce"], str)
        or re.fullmatch(r"[0-9a-f]{24}", record["nonce"]) is None
    ):
        result["state"] = "job_binding_changed"
        return result
    issued, expires = record["issued_epoch"], record["expires_epoch"]
    current = int(clock.timestamp())
    if (
        issued < int(claimed_at.timestamp())
        or issued > int(completed_at.timestamp())
        or issued > current + 2
        or expires < issued + _MIN_LEASE_SECONDS
        or expires - issued > _MAX_TTL_SECONDS
    ):
        result["state"] = "historical_window_invalid"
        return result
    return {
        **result,
        "state": "signed_historical_heartbeat_verified",
        "historical_heartbeat_verified": True,
        "historical_receipt_age_seconds": max(0, current - issued),
    }


EVIDENCE_FIELD = _EVIDENCE_FIELD
