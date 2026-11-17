"""Opt-in, private R2/Worker delivery for saved Etsy Listing Image derivatives.

OFF by default. No browser ticket is ever created before the normal tenant,
Picked and principal checks in the gallery router. Only 192px thumbnails and
720px WebP derivatives can be staged; never upload original/Native Master PNG.
Worker HEAD probes are bounded and fail back to CAM + Drive on any error.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import logging
import os
import re
import time
from urllib.parse import urlsplit

import httpx

_LOG = logging.getLogger("app.etsy_spy_image_cdn")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PACK = re.compile(r"^sp-[0-9a-f]{40}$")
_ALLOWED = {"thumbnail": ("png", "image/png"), "preview": ("webp", "image/webp")}


@dataclass(frozen=True)
class CdnConfig:
    base_url: str
    key: bytes


def cdn_config() -> CdnConfig | None:
    if os.getenv("CAM_ETSY_IMAGE_CDN_ENABLED", "").lower() not in {"1", "true"}:
        return None
    url = os.getenv("CAM_ETSY_IMAGE_CDN_WORKER_URL", "").rstrip("/")
    secret = os.getenv("CAM_ETSY_IMAGE_CDN_HMAC_HEX", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.port
            or parsed.path or parsed.query or parsed.fragment
            or not re.fullmatch(r"[0-9a-fA-F]{64,128}", secret)):
        # Fails closed and does not log secrets or URLs.
        _LOG.error("etsy_image_cdn_disabled_invalid_configuration")
        return None
    return CdnConfig(base_url=url, key=bytes.fromhex(secret))


def object_key(config: CdnConfig, *, tenant_id: str, pack_id: str,
               slot: int, quality: str, sha256: str) -> str:
    if (not _PACK.fullmatch(pack_id) or not 1 <= slot <= 10 or
            quality not in _ALLOWED or not _SHA.fullmatch(sha256)):
        raise ValueError("Invalid private image CDN object")
    tenant_hash = hmac.new(config.key, tenant_id.encode("utf-8"),
                           hashlib.sha256).hexdigest()[:24]
    extension, _ = _ALLOWED[quality]
    version = "-w720v1" if quality == "preview" else ""
    return f"v1/{tenant_hash}/{pack_id}/{slot:02d}/{quality}/{sha256}{version}.{extension}"


def ticket(config: CdnConfig, method: str, key: str, *,
           now: int | None = None, body_sha256: str | None = None) -> tuple[str, dict]:
    if method not in {"GET", "HEAD", "PUT"}:
        raise ValueError("Invalid CDN method")
    expires = int(time.time() if now is None else now) + 45
    path = "/v1/media/" + key
    message = f"{method}\n{path}\n{expires}"
    headers = {}
    if method == "PUT":
        if not isinstance(body_sha256, str) or not _SHA.fullmatch(body_sha256):
            raise ValueError("Invalid CDN upload digest")
        message += "\n" + body_sha256
        headers = {"X-CAM-Expiry": str(expires),
                   "X-CAM-Digest": body_sha256}
    mac = hmac.new(config.key, message.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    if method == "PUT":
        headers["X-CAM-Signature"] = mac
        return config.base_url + path, headers
    return (config.base_url + path + f"?exp={expires}&sig={mac}", headers)


async def available_cdn_url(
    config: CdnConfig | None, *, tenant_id: str, pack_id: str,
    slot: int, quality: str, sha256: str,
) -> str | None:
    if config is None or quality not in _ALLOWED:
        return None
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    head_url, _ = ticket(config, "HEAD", key)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.0),
                                     follow_redirects=False) as client:
            response = await client.head(head_url)
        if response.status_code == 200:
            url, _ = ticket(config, "GET", key)
            return url
    except (httpx.HTTPError, ValueError):
        pass
    return None


async def stage_derivative(
    config: CdnConfig | None, *, tenant_id: str, pack_id: str,
    slot: int, quality: str, sha256: str, data: bytes,
) -> None:
    """Best-effort upload. The primary CAM response never depends on R2."""
    if (config is None or quality not in _ALLOWED
            or not 0 < len(data) <= 2 * 1024 * 1024):
        return
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    digest = hashlib.sha256(data).hexdigest()
    put_url, headers = ticket(config, "PUT", key, body_sha256=digest)
    headers["Content-Type"] = _ALLOWED[quality][1]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=2.0),
                                     follow_redirects=False) as client:
            response = await client.put(put_url, content=data, headers=headers)
            if response.status_code not in {200, 201, 204}:
                _LOG.warning("etsy_image_cdn_stage_fallback status=%s", response.status_code)
    except httpx.HTTPError:
        _LOG.warning("etsy_image_cdn_stage_fallback transport=unavailable")
