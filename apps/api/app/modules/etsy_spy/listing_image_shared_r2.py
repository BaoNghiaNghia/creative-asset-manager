"""Private Etsy thumbnail/WebP cache reusing the existing CAM R2 video bucket.

No new bucket, Worker, token, credentials, or public object namespace.
Enabled per tenant. Signed edge delivery is a separate gate because the
currently deployed video Worker accepts *only* /video-cache/ paths.
Always authorize via the caller's Picked/tenant ACL before invoking this.
Native Master and original Listing PNG are expressly forbidden.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import logging
import os
import re
import time
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from .request_metrics import ETSY_PERFORMANCE
from .thumbnail_memory_cache import ThumbnailMemoryCache

_LOG = logging.getLogger("app.etsy_spy_r2_cache")
_PACK = re.compile(r"sp-[0-9a-f]{40}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TYPES = {"thumbnail": ("image/png", ".png"), "preview": ("image/webp", "-w720v1.webp")}
_MAX_OBJECT_BYTES = 2 * 1024 * 1024
# Leave a two-minute margin before the 30-day deadline so a 90-second
# in-process memory hit never outlives the immutable R2 object's lifespan.
_MAX_ORIGIN_AGE_SECONDS = 30 * 86400 - 120
# Memory-only reuse after the tenant + Picked ACL. Keep small on this VPS:
# 16 MiB per API process, 90 seconds; no PNG/preview persisted locally.
_DERIVATIVE_MEMORY = ThumbnailMemoryCache(
    max_bytes=16 * 1024 * 1024, max_item_bytes=_MAX_OBJECT_BYTES,
    ttl_seconds=90,
)
_MAX_DAILY_UPLOAD_BYTES = 128 * 1024 * 1024  # per process, small initial canary
_READ_SLOTS = BoundedSemaphore(4)
_WRITE_SLOTS = BoundedSemaphore(2)
_LOCK = Lock()
_STATUS: OrderedDict[str, tuple[bool, float]] = OrderedDict()
_ACTIVE_WRITES: set[str] = set()
_MAX_STATUS = 2048
_DAILY_EPOCH = 0
_DAILY_BYTES = 0
_WORKER_STATUS: dict[str, tuple[bool, float]] = {}


@dataclass(frozen=True)
class ExistingR2Config:
    bucket: str
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    signing_secret: str = field(repr=False)
    worker_origin: str = ""
    worker_enabled: bool = False


def config_for_tenant(tenant_id: str) -> ExistingR2Config | None:
    if os.getenv("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "").lower() not in {"true", "1"}:
        return None
    # No '*' wildcard, even when another toggle is set accidentally.
    allowlist = set(filter(None, (v.strip() for v in
        os.getenv("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "").split(","))))
    if not tenant_id or tenant_id not in allowlist:
        return None
    settings = get_settings()
    if not (settings.R2_VIDEO_CACHE_ENABLED and settings.R2_BUCKET_NAME and
            settings.R2_ACCESS_KEY_ID.get_secret_value() and
            settings.R2_SECRET_ACCESS_KEY.get_secret_value() and
            settings.R2_VIDEO_MEDIA_SIGNING_SECRET.get_secret_value()):
        return None
    if not settings.r2_endpoint_url.startswith("https://"):
        return None
    origin = settings.video_media_base_url
    parsed = urlsplit(origin)
    if not origin or parsed.scheme != "https" or not parsed.hostname or (
            parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        origin = ""
    worker_enabled = (
        os.getenv("CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED", "").lower() in {"true", "1"}
        and bool(origin)
    )
    return ExistingR2Config(
        bucket=settings.R2_BUCKET_NAME,
        endpoint=settings.r2_endpoint_url,
        access_key=settings.R2_ACCESS_KEY_ID.get_secret_value(),
        secret_key=settings.R2_SECRET_ACCESS_KEY.get_secret_value(),
        signing_secret=settings.R2_VIDEO_MEDIA_SIGNING_SECRET.get_secret_value(),
        worker_origin=origin,
        worker_enabled=worker_enabled,
    )


def object_key(config: ExistingR2Config, *, tenant_id: str, pack_id: str,
               slot: int, quality: str, sha256: str) -> str:
    if (not tenant_id or not _PACK.fullmatch(pack_id) or not isinstance(slot, int)
            or type(slot) is not int or not 1 <= slot <= 13 or quality not in _TYPES
            or not _SHA.fullmatch(sha256)):
        raise ValueError("Invalid R2 Etsy image derivative identity")
    # Domain separation protects the existing video namespace and conceals raw
    # tenant IDs in R2 keys. The immutable source SHA versions the derivative.
    secret = config.signing_secret.encode("utf-8")
    tenant_hash = hmac.new(secret,
        b"etsy-image-tenant-v1\0" + tenant_id.encode("utf-8"),
        "sha256").hexdigest()[:24]
    return ("etsy-images/v1/" + tenant_hash + "/" + pack_id + "/" +
            f"{slot:02d}/" + quality + "/" + sha256 + _TYPES[quality][1])


def signed_worker_url(config: ExistingR2Config, key: str, *,
                      now: int | None = None) -> str | None:
    if not config.worker_enabled or not config.worker_origin or not key.startswith("etsy-images/v1/"):
        return None
    expiry = int(time.time() if now is None else now) + 45
    pathname = "/" + key
    message = f"v1\netsy-image-read\n{pathname}\n{expiry}".encode("utf-8")
    signature = hmac.new(
        config.signing_secret.encode("utf-8"), message, "sha256").digest()
    token = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return config.worker_origin + pathname + f"?v=1&exp={expiry}&sig={token}"


async def worker_ready(config: ExistingR2Config) -> bool:
    """Guard browser redirects by an independent, signed synthetic HEAD.

    An existing video-only Worker rejects this route with 403. Only a 404
    returned AFTER image-ticket verification permits turning on edge delivery.
    Limit the probe to one per worker per short TTL; network failures fall back
    to authenticated R2/Drive without browser-visible broken redirects.
    """
    if not config.worker_enabled:
        return False
    probe_id = sha256((config.worker_origin + config.signing_secret).encode()).hexdigest()
    with _LOCK:
        cached = _WORKER_STATUS.get(probe_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
    sample = object_key(
        config, tenant_id="__cam_image_cdn_probe__", pack_id="sp-" + "0" * 40,
        slot=1, quality="thumbnail", sha256="0" * 64)
    url = signed_worker_url(config, sample)
    if not url:
        return False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(1.5, connect=0.5),
            follow_redirects=False,
        ) as client:
            response = await client.head(url)
        ready = response.status_code == 404
    except httpx.HTTPError:
        ready = False
    with _LOCK:
        _WORKER_STATUS[probe_id] = (
            ready, time.monotonic() + (60 if ready else 10))
        if len(_WORKER_STATUS) > 32:
            _WORKER_STATUS.clear()
    return ready


def _cached(key: str) -> bool | None:
    with _LOCK:
        row = _STATUS.get(key)
        if row is None:
            return None
        found, until = row
        if until <= time.monotonic():
            _STATUS.pop(key, None)
            return None
        _STATUS.move_to_end(key)
        return found


def _mark(key: str, found: bool, ttl: float | None = None) -> None:
    with _LOCK:
        _STATUS[key] = (found, time.monotonic() + (
            ttl if ttl is not None else (300 if found else 15)))
        _STATUS.move_to_end(key)
        while len(_STATUS) > _MAX_STATUS:
            _STATUS.popitem(last=False)


def _client(config: ExistingR2Config):
    # Reuse the already installed boto3/botocore used by CAM video cache.
    # Fresh short-timeout clients are safe when the R2 endpoint is unavailable.
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=config.endpoint, region_name="auto",
        aws_access_key_id=config.access_key, aws_secret_access_key=config.secret_key,
        config=Config(s3={"addressing_style": "path"},
                      retries={"max_attempts": 1},
                      connect_timeout=1.0, read_timeout=3.0),
    )


def _read(config: ExistingR2Config, key: str, expected_type: str) -> bytes | None:
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        response = _client(config).get_object(Bucket=config.bucket, Key=key)
        body = response["Body"]
        try:
            length = int(response.get("ContentLength") or 0)
            modified = response.get("LastModified")
            age_seconds = (time.time() - modified.timestamp()
                           if hasattr(modified, "timestamp") else 31 * 86400)
            # R2 image cache is disposable: never serve beyond the fixed
            # 30-day age even if the bucket lifecycle rule is unavailable.
            if not 0 <= age_seconds < _MAX_ORIGIN_AGE_SECONDS:
                return None
            if not 0 < length <= _MAX_OBJECT_BYTES or response.get("ContentType") != expected_type:
                return None
            expected_sha = response.get("Metadata", {}).get("cam-sha256", "")
            if not _SHA.fullmatch(expected_sha):
                return None
            value = body.read(_MAX_OBJECT_BYTES + 1)
            if len(value) != length or sha256(value).hexdigest() != expected_sha:
                return None
            return value
        finally:
            body.close()
    except (BotoCoreError, ClientError, OSError, ValueError, KeyError):
        return None


async def read_derivative(config: ExistingR2Config, *, tenant_id: str,
                          pack_id: str, slot: int, quality: str,
                          sha256: str) -> bytes | None:
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    memory_key = (tenant_id, key, quality)
    memory_hit = _DERIVATIVE_MEMORY.get(memory_key)
    if memory_hit is not None:
        ETSY_PERFORMANCE.cache(True, label="listing_memory_" + quality)
        return memory_hit
    ETSY_PERFORMANCE.cache(False, label="listing_memory_" + quality)
    if _cached(key) is False:
        ETSY_PERFORMANCE.cache(False, label="listing_r2_" + quality)
        return None
    if not _READ_SLOTS.acquire(blocking=False):
        return None
    try:
        value = await asyncio.to_thread(_read, config, key, _TYPES[quality][0])
    finally:
        _READ_SLOTS.release()
    _mark(key, value is not None)
    ETSY_PERFORMANCE.cache(value is not None, label="listing_r2_" + quality)
    if value is not None:
        _DERIVATIVE_MEMORY.put(memory_key, value)
    return value


def _write(config: ExistingR2Config, key: str, content_type: str,
           value: bytes) -> bool:
    """Upload only when absent: no view/retry may renew R2 object age."""
    from botocore.exceptions import BotoCoreError, ClientError
    digest = sha256(value).hexdigest()
    client = _client(config)
    try:
        response = client.put_object(
            Bucket=config.bucket, Key=key, Body=value,
            ContentType=content_type, Metadata={"cam-sha256": digest},
            CacheControl="private, no-store", IfNoneMatch="*",
        )
        return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code") or "") not in (
                "PreconditionFailed", "412"):
            return False
        # Concurrent create succeeded elsewhere. Only treat the existing
        # derivative as ready after metadata matches the exact verified bytes.
        try:
            head = client.head_object(Bucket=config.bucket, Key=key)
            return (
                head.get("ContentType") == content_type
                and head.get("Metadata", {}).get("cam-sha256") == digest
                and int(head.get("ContentLength", -1)) == len(value)
            )
        except (BotoCoreError, ClientError, OSError, TypeError, ValueError):
            return False
    except (BotoCoreError, OSError):
        return False


async def stage_derivative(config: ExistingR2Config, *, tenant_id: str,
                           pack_id: str, slot: int, quality: str,
                           sha256: str, data: bytes) -> None:
    if quality not in _TYPES or not 0 < len(data) <= _MAX_OBJECT_BYTES:
        return
    key = object_key(config, tenant_id=tenant_id, pack_id=pack_id,
                     slot=slot, quality=quality, sha256=sha256)
    if _cached(key) is True or not _WRITE_SLOTS.acquire(blocking=False):
        return
    global _DAILY_EPOCH, _DAILY_BYTES
    with _LOCK:
        day = int(time.time() // 86400)
        if day != _DAILY_EPOCH:
            _DAILY_EPOCH = day
            _DAILY_BYTES = 0
        if key in _ACTIVE_WRITES or _DAILY_BYTES + len(data) > _MAX_DAILY_UPLOAD_BYTES:
            _WRITE_SLOTS.release()
            return
        _DAILY_BYTES += len(data)
        _ACTIVE_WRITES.add(key)
    started = time.monotonic()
    status = 503
    try:
        success = await asyncio.to_thread(_write, config, key, _TYPES[quality][0], data)
        status = 200 if success else 503
        _mark(key, success, ttl=300 if success else 5)
    finally:
        ETSY_PERFORMANCE.record(
            "listing_r2_stage", status_code=status,
            duration_ms=(time.monotonic() - started) * 1000)
        with _LOCK:
            _ACTIVE_WRITES.discard(key)
        _WRITE_SLOTS.release()
