"""Offline signed, NON-EXECUTABLE Branding DNA preflight envelope.

Ed25519 separates API signing authority from a worker holding only a public
verification key. This deliberately does not issue jobs, create grants, or
integrate with /lease. Both runtimes use the same ASCII key=value framing.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FIELDS = (
    "agent_id", "expires_at", "issued_at", "key_id", "lease_generation",
    "lease_id", "max_gallery_items", "max_pages", "nonce", "platform",
    "purpose", "scan_enabled", "scope_sha256", "shop_id", "snapshot_id",
    "task_id", "tenant_id", "version",
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
IDS = ("tenant_id", "shop_id", "task_id", "snapshot_id", "agent_id", "lease_id", "nonce")


def validate_preflight_claims(claims: Mapping, *, now: int) -> None:
    if not isinstance(claims, dict) or set(claims) != set(FIELDS):
        raise ValueError("invalid_envelope_fields")
    if type(now) is not int or now < 1:
        raise ValueError("invalid_current_time")
    if claims["version"] != 1 or type(claims["version"]) is not int:
        raise ValueError("invalid_envelope_version")
    if claims["purpose"] != "preflight_only" or claims["scan_enabled"] is not False:
        raise ValueError("envelope_cannot_enable_scan")
    if claims["platform"] not in ("amazon", "etsy"):
        raise ValueError("invalid_platform")
    if not isinstance(claims["key_id"], str) or not KEY_RE.fullmatch(claims["key_id"]):
        raise ValueError("invalid_key_id")
    if not isinstance(claims["scope_sha256"], str) or not HASH_RE.fullmatch(claims["scope_sha256"]):
        raise ValueError("invalid_scope_digest")
    if any(not isinstance(claims[k], str) or not UUID_RE.fullmatch(claims[k]) for k in IDS):
        raise ValueError("invalid_envelope_identity")
    for field, upper in (("lease_generation", 1_000_000), ("max_pages", 5), ("max_gallery_items", 20)):
        if type(claims[field]) is not int or not 1 <= claims[field] <= upper:
            raise ValueError("invalid_" + field)
    issued, expires = claims["issued_at"], claims["expires_at"]
    if type(issued) is not int or type(expires) is not int:
        raise ValueError("invalid_envelope_time")
    if not (now - 300 <= issued <= now + 30 and now < expires <= issued + 300 and expires > issued):
        raise ValueError("envelope_expired_or_outside_window")


def envelope_signing_bytes(claims: Mapping) -> bytes:
    """Unambiguous fixed-order ASCII framing shared with the Windows worker."""
    material = "branding-dna-preflight-v1\n"
    for key in FIELDS:
        value = "false" if key == "scan_enabled" else str(claims[key])
        material += f"{key}={value}\n"
    return material.encode("ascii")


def sign_preflight_envelope(claims: dict, private_key: Ed25519PrivateKey, *,
                            now: int | None = None) -> dict:
    """Signer is internal only. Private key must come from an authorized key store."""
    current = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    validate_preflight_claims(claims, now=current)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("invalid_signing_key")
    signature = private_key.sign(envelope_signing_bytes(claims))
    return {**claims, "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")}