"""Opt-in, private R2/Worker delivery for saved Etsy Listing Image derivatives.

OFF by default. No browser ticket is ever created before the normal tenant,
Picked and principal checks in the gallery router. Only 192px thumbnails and
720px WebP derivatives can be staged; never upload original/Native Master PNG.
Worker HEAD probes are bounded and fail back to CAM + Drive on any error.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import logging
import os
import re
import time
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

import httpx

from .request_metrics import ETSY_PERFORMANCE

_LOG = logging.getLogger("app.etsy_spy_image_cdn")
_STATUS_LOCK = Lock()
_OBJECT_STATUS: OrderedDict[str, tuple[bool, float]] = OrderedDict()
_MAX_CACHED_OBJECTS = 2048
_PRESENT_TTL_SECONDS = 300
_MISSING_TTL_SECONDS = 20
_STAGE_SLOTS = BoundedSemaphore(2)
_STAGING: set[str] = set()
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PACK = re.compile(r"^sp-[0-9a-f]{40}$")
_ALLOWED = {"thumbnail": ("png", "image/png"), "preview": ("webp", "image/webp")}


@dataclass(frozen=True)
class CdnConfig:
    base_url: str
    key: bytes


def cdn_config(*, tenant_id: str) -> CdnConfig | None:
    if os.getenv("CAM_ETSY_IMAGE_CDN_ENABLED", "").lower() not in {"1", "true"}:
        return None
    # Explicit tenant canary is mandatory. A misconfigured global flag cannot
    # unexpectedly redirect all tenants to a not-yet-provisioned Worker.
    canaries = {value.strip() for value in
                os.getenv("CAM_ETSY_IMAGE_CDN_CANARY_TENANTS", "").split(",")
                if value.strip()}
    if not tenant_id or (tenant_id not in canaries and "*" not in canaries):
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


def _status_key(config: CdnConfig, key: str) -> str:
    return config.base_url + "/" + key


def _cached_status(cache_key: str) -> bool | None:
    """Read a bounded process-local availability cache; never cache signed URLs."""
    with _STATUS_LOCK:
        cached = _OBJECT_STATUS.get(cache_key)
        if cached is None:
            return None
        available, deadline = cached
        if deadline <= time.monotonic():
            _OBJECT_STATUS.pop(cache_key, None)
            return None
        _OBJECT_STATUS.move_to_end(cache_key)
        return available


def _remember_status(cache_key: str, *, available: bool,
                     ttl: float | None = None) -> None:
    lifetime = ttl if ttl is not None else (
        _PRESENT_TTL_SECONDS if available else _MISSING_TTL_SECONDS)
    with _STATUS_LOCK:
        _OBJECT_STATUS[cache_key] = (available, time.monotonic() + lifetime)
        _OBJECT_STATUS.move_to_end(cache_key)
        while len(_OBJECT_STATUS) > _MAX_CACHED_OBJECTS:
            _OBJECT_STATUS.popitem(last=False)


async def available_cdn_url(
    config: CdnConfig | None, *, tenant_id: str, pack_id: str,
    slot: int, quality: str, sha256: str,
) -> str | None:
    if config is None or quality not in _ALLOWED:
        return None
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    cache_key = _status_key(config, key)
    available = _cached_status(cache_key)
    if available is None:
        head_url, _ = ticket(config, "HEAD", key)
        # Probe only once per brief TTL. A slow or unavailable Worker must
        # never hold every CAM image response for several seconds.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.5),
                                         follow_redirects=False) as client:
                response = await client.head(head_url)
            if response.status_code == 200:
                available = True
                _remember_status(cache_key, available=True)
            elif response.status_code == 404:
                available = False
                _remember_status(cache_key, available=False)
            else:
                available = False
                _remember_status(cache_key, available=False, ttl=5)
        except (httpx.HTTPError, ValueError):
            available = False
            _remember_status(cache_key, available=False, ttl=5)
    ETSY_PERFORMANCE.cache(bool(available), label="listing_cdn_" + quality)
    if available:
        url, _ = ticket(config, "GET", key)
        return url
    return None


async def stage_derivative(
    config: CdnConfig | None, *, tenant_id: str, pack_id: str,
    slot: int, quality: str, sha256: str, data: bytes,
) -> None:
    """Best-effort upload, bounded to two concurrent R2 writes per API process."""
    if (config is None or quality not in _ALLOWED
            or not 0 < len(data) <= 2 * 1024 * 1024):
        return
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    cache_key = _status_key(config, key)
    if _cached_status(cache_key) is True:
        return
    # Never accumulate unbounded BackgroundTasks when the server is busy.
    if not _STAGE_SLOTS.acquire(blocking=False):
        return
    with _STATUS_LOCK:
        if cache_key in _STAGING:
            _STAGE_SLOTS.release()
            return
        _STAGING.add(cache_key)
    started = time.monotonic()
    status_code = 503
    try:
        digest = hashlib.sha256(data).hexdigest()
        put_url, headers = ticket(config, "PUT", key, body_sha256=digest)
        headers["Content-Type"] = _ALLOWED[quality][1]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=2.0),
                                         follow_redirects=False) as client:
                response = await client.put(put_url, content=data, headers=headers)
            status_code = response.status_code
            if status_code in {200, 201, 204}:
                _remember_status(cache_key, available=True)
            else:
                _remember_status(cache_key, available=False, ttl=5)
                _LOG.warning("etsy_image_cdn_stage_fallback status=%s", status_code)
        except httpx.HTTPError:
            _remember_status(cache_key, available=False, ttl=5)
            _LOG.warning("etsy_image_cdn_stage_fallback transport=unavailable")
    finally:
        ETSY_PERFORMANCE.record("listing_cdn_stage", status_code=status_code,
                               duration_ms=(time.monotonic() - started) * 1000)
        with _STATUS_LOCK:
            _STAGING.discard(cache_key)
        _STAGE_SLOTS.release()
