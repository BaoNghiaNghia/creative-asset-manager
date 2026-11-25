"""Short-lived, reference-preview-only SMS import capability.

Minted by a paired Scout for a current Picked product. No original image
is returned, no Drive ACL is changed, no durable media copy is created.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import re
import time
from PIL import Image
from fastapi import HTTPException

DIGEST = re.compile(r"[a-f0-9]{64}\Z")
AGENT_ID = re.compile(r"[a-f0-9-]{36}\Z")
LISTING_ID = re.compile(r"[0-9]{5,20}\Z")
TTL = 300


def signature(secret, agent_id, tenant, listing_id, digest, expiry):
    if (not isinstance(secret, str) or len(secret) < 32
            or not AGENT_ID.fullmatch(agent_id)
            or not LISTING_ID.fullmatch(listing_id)
            or not DIGEST.fullmatch(digest)):
        raise ValueError("Invalid reference preview capability")
    # Tenant identity is length-prefixed, not supplied in the public URL.
    msg = f"sms-design-preview-v1\n{agent_id}\n{len(tenant)}:{tenant}\n{listing_id}\n{digest}\n{expiry}"
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(secret, agent_id, tenant, listing_id, digest, expiry, token, now=None):
    now = int(time.time()) if now is None else now
    if (not isinstance(expiry, int) or not now < expiry <= now + TTL
            or not isinstance(token, str) or not DIGEST.fullmatch(token)):
        return False
    try:
        expected = signature(secret, agent_id, tenant, listing_id, digest, expiry)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, token)


def render_preview(raw, expected_sha):
    if len(raw) > 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha:
        raise HTTPException(409, "Design source changed")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != "PNG" or source.width * source.height > 24_000_000:
                raise ValueError("Invalid design preview source")
            image = source.convert("RGBA")
            image.thumbnail((720, 720), Image.Resampling.LANCZOS)
            background = Image.new("RGBA", image.size, (235, 235, 235, 255))
            background.alpha_composite(image)
            result = io.BytesIO()
            background.convert("RGB").save(result, "PNG", optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Could not render reference preview") from exc
    output = result.getvalue()
    if not 0 < len(output) <= 2 * 1024 * 1024:
        raise HTTPException(413, "Reference preview too large")
    return output
