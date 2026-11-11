"""Short-lived server-signed device-reported idle observation.

NOT a device hardware/browser readiness attestation. This records only that a
paired, nonrevoked Image Agent credential reported an empty local journal while
CAM saw no unresolved image job claimed by that agent. Stateless receipt:
no database writes, no migration, no slot lease, no dispatch authorization.
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

_PROTOCOL = "cam-image-idle-report-v1"
_ATTESTATION_PROTOCOL = "cam-image-idle-attestation-v1"
_TTL_SECONDS = 30
_MAX_CLOCK_SKEW_SECONDS = 2
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX24 = re.compile(r"^[0-9a-f]{24}$")
_FIELDS = frozenset({
    "protocol", "tenant_id", "agent_id", "session_id", "sequence",
    "device_mode", "reported_journal_empty", "issued_epoch",
    "expires_epoch", "nonce", "signature",
})


def idle_attestation_enabled() -> bool:
    """Requires both the existing heartbeat opt-in and a new narrow flag."""
    return (
        os.getenv("CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED") == "1"
        and os.getenv("CAM_AGENT_HARNESS_IDLE_ATTESTATION_ENABLED", "0") == "1"
    )


def _clock(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    return (moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None
            else moment.astimezone(timezone.utc))


def _mac(key: bytes, message: dict[str, Any]) -> str:
    blob = json.dumps(
        message, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hmac.new(key, b"cam-idle-attestation-v1:" + blob, hashlib.sha256).hexdigest()


def issue_idle_attestation(
    *, tenant_id: str, agent_id: str, session_id: str,
    sequence: int, signing_key: bytes, now: datetime | None = None,
) -> dict[str, Any]:
    """Mint a bounded receipt; caller first authenticates bearer and checks DB."""
    if (
        len(signing_key) != 32
        or type(sequence) is not int
        or not 1 <= sequence <= 2147483647
    ):
        raise ValueError("idle_attestation_invalid_contract")
    issued = int(_clock(now).timestamp())
    unsigned = {
        "protocol": _ATTESTATION_PROTOCOL,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "sequence": sequence,
        "device_mode": "orchestrator_v1",
        "reported_journal_empty": True,
        "issued_epoch": issued,
        "expires_epoch": issued + _TTL_SECONDS,
        "nonce": secrets.token_hex(12),
    }
    return {**unsigned, "signature": _mac(signing_key, unsigned)}


def verify_idle_attestation(
    receipt: Any, *, signing_key: bytes | None,
    tenant_id: str, agent_id: str,
    agent_active: bool, unresolved_claims: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only verification; always zero reservable slots.

    This checks server-signed credential activity, NOT the truth of a local
    browser/journal claim. The device may become busy immediately; hence this
    must never be used as an atomic resource reservation.
    """
    result: dict[str, Any] = {
        "state": "unverified",
        "paired_idle_report_authenticated": False,
        "independent_browser_idle_verified": False,
        "server_unresolved_claims": bool(unresolved_claims),
        "reported_mode": None,
        "receipt_age_seconds": None,
        "receipt_expires_in_seconds": 0,
        "verified_free_slots": 0,
        "reservation_created": False,
        "dispatch_allowed": False,
        "execution_enabled": False,
    }
    if signing_key is None or len(signing_key) != 32:
        return {**result, "state": "signing_key_unavailable"}
    if not agent_active:
        return {**result, "state": "device_revoked"}
    if unresolved_claims:
        return {**result, "state": "unresolved_processing_claim"}
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS:
        return {**result, "state": "receipt_shape_invalid"}
    signature = receipt.get("signature")
    unsigned = {name: value for name, value in receipt.items() if name != "signature"}
    if (
        not isinstance(signature, str)
        or not _HEX64.fullmatch(signature)
        or not hmac.compare_digest(signature, _mac(signing_key, unsigned))
    ):
        return {**result, "state": "signature_invalid"}
    import uuid
    try:
        valid_uuid = str(uuid.UUID(str(receipt["session_id"]))) == receipt["session_id"]
    except (ValueError, AttributeError, TypeError):
        valid_uuid = False
    if (
        receipt["protocol"] != _ATTESTATION_PROTOCOL
        or receipt["tenant_id"] != tenant_id
        or receipt["agent_id"] != agent_id
        or receipt["device_mode"] != "orchestrator_v1"
        or receipt["reported_journal_empty"] is not True
        or type(receipt["sequence"]) is not int
        or not 1 <= receipt["sequence"] <= 2147483647
        or not valid_uuid
        or type(receipt["issued_epoch"]) is not int
        or type(receipt["expires_epoch"]) is not int
        or not isinstance(receipt["nonce"], str)
        or _HEX24.fullmatch(receipt["nonce"]) is None
    ):
        return {**result, "state": "receipt_binding_invalid"}
    now_epoch = int(_clock(now).timestamp())
    issued, expires = receipt["issued_epoch"], receipt["expires_epoch"]
    if (
        issued > now_epoch + _MAX_CLOCK_SKEW_SECONDS
        or expires <= now_epoch
        or expires - issued != _TTL_SECONDS
        or now_epoch - issued > _TTL_SECONDS
    ):
        return {**result, "state": "receipt_expired"}
    return {
        **result,
        "state": "authenticated_device_reported_idle",
        "paired_idle_report_authenticated": True,
        "reported_mode": "orchestrator_v1",
        "receipt_age_seconds": max(0, now_epoch - issued),
        "receipt_expires_in_seconds": max(0, expires - now_epoch),
    }


IDLE_REPORT_PROTOCOL = _PROTOCOL
