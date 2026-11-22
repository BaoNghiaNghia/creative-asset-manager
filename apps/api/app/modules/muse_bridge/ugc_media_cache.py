"""Disposable, private Muse UGC playback and poster derivatives in CAM's EXISTING R2 bucket.

Drive is the sole canonical copy. No new bucket, DB, master or persistent VPS
file is created. R2 objects are written only during verified ingestion or an
explicit operator backfill, never by viewing. The existing video-cache/ 30-day
lifecycle and Worker expiry apply to every derivative.

Enabling is explicit and tenant allowlisted; a CDN failure never blocks the
Google Drive upload or the existing private playback endpoint.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import io
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from threading import Lock
from urllib.parse import urlsplit

from PIL import Image


_LOG = logging.getLogger("app.muse_ugc_cache")
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MP4 = 40 * 1024 * 1024
_MAX_POSTER = 700_000
_CACHE_LOCK = Lock()
_CACHE_STATUS: OrderedDict[str, tuple[bool, float]] = OrderedDict()
_WORKER_STATUS: dict[str, tuple[bool, float]] = {}
_MAX_STATUS = 1024


@dataclass(frozen=True)
class UgcCacheConfig:
    bucket: str
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    signing_secret: str = field(repr=False)
    worker_origin: str = ""
    edge_enabled: bool = False


def config_for_tenant(tenant: str) -> UgcCacheConfig | None:
    # Reuse the pre-existing, explicitly approved Etsy derivative R2 canary
    # unless the independent UGC flag overrides it. Both routes stay private;
    # edge redirects remain a separate opt-in and require signed HEAD health.
    enabled = os.getenv("CAM_MUSE_UGC_R2_ENABLED",
                        os.getenv("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "")).lower()
    if enabled not in {"true", "1"}:
        return None
    allowed_raw = os.getenv("CAM_MUSE_UGC_R2_CANARY_TENANTS",
                            os.getenv("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", ""))
    allowed = set(filter(None, (v.strip() for v in allowed_raw.split(","))))
    if not _TENANT.fullmatch(tenant or "") or tenant not in allowed:
        return None
    from app.core.config import get_settings
    s = get_settings()
    if (not s.R2_VIDEO_CACHE_ENABLED or not s.R2_BUCKET_NAME
            or not s.R2_ACCESS_KEY_ID.get_secret_value()
            or not s.R2_SECRET_ACCESS_KEY.get_secret_value()
            or not s.R2_VIDEO_MEDIA_SIGNING_SECRET.get_secret_value()
            or not s.r2_endpoint_url.startswith("https://")):
        return None
    origin = s.video_media_base_url
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path or
                parsed.query or parsed.fragment or parsed.port or
                parsed.username or parsed.password):
            origin = ""
    except ValueError:
        origin = ""
    return UgcCacheConfig(
        bucket=s.R2_BUCKET_NAME,
        endpoint=s.r2_endpoint_url,
        access_key=s.R2_ACCESS_KEY_ID.get_secret_value(),
        secret_key=s.R2_SECRET_ACCESS_KEY.get_secret_value(),
        signing_secret=s.R2_VIDEO_MEDIA_SIGNING_SECRET.get_secret_value(),
        worker_origin=origin,
        edge_enabled=os.getenv("CAM_MUSE_UGC_EDGE_ENABLED", "").lower() in {"true", "1"}
            and bool(origin) and bool(s.video_delivery_configured),
    )


def object_key(tenant: str, source_sha: str, kind: str) -> str:
    if not _TENANT.fullmatch(tenant or "") or not _SHA.fullmatch(source_sha or ""):
        raise ValueError("Invalid Muse UGC cache identity")
    if kind == "playback":
        return f"video-cache/{tenant}/{source_sha}/playback.mp4"
    if kind in ("poster320", "poster640"):
        return f"video-cache/{tenant}/{source_sha}/poster-{kind[-3:]}.webp"
    raise ValueError("Invalid Muse UGC derivative kind")


def _client(config: UgcCacheConfig):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=config.endpoint, region_name="auto",
        aws_access_key_id=config.access_key, aws_secret_access_key=config.secret_key,
        config=Config(s3={"addressing_style": "path"},
                      retries={"max_attempts": 1}, connect_timeout=2, read_timeout=9),
    )


def _status(key: str) -> bool | None:
    with _CACHE_LOCK:
        row = _CACHE_STATUS.get(key)
        if row is None or row[1] < time.monotonic():
            _CACHE_STATUS.pop(key, None)
            return None
        _CACHE_STATUS.move_to_end(key)
        return row[0]


def _mark(key: str, hit: bool) -> None:
    with _CACHE_LOCK:
        _CACHE_STATUS[key] = (hit, time.monotonic() + (35 if hit else 8))
        _CACHE_STATUS.move_to_end(key)
        while len(_CACHE_STATUS) > _MAX_STATUS:
            _CACHE_STATUS.popitem(last=False)


def _is_missing(exc: Exception) -> bool:
    from botocore.exceptions import ClientError
    return isinstance(exc, ClientError) and str(exc.response.get("Error", {}).get("Code")) in (
        "404", "NoSuchKey", "NotFound",
    )


def _head(client, config: UgcCacheConfig, key: str, source_sha: str, kind: str) -> bool:
    try:
        entry = client.head_object(Bucket=config.bucket, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return False
        raise
    metadata = entry.get("Metadata") or {}
    typ = "video/mp4" if kind == "playback" else "image/webp"
    modified = entry.get("LastModified")
    # No cache renewal from reads: refuse an expired or wrong-content object.
    age = (time.time() - modified.timestamp()) if hasattr(modified, "timestamp") else 31 * 86400
    return (metadata.get("cam-source-sha256") == source_sha
            and entry.get("ContentType") == typ
            and 0 < int(entry.get("ContentLength", 0)) <= (
                _MAX_MP4 + 2 * 1024 * 1024 if kind == "playback" else _MAX_POSTER)
            and 0 <= age < 30 * 86400)


def _has_object(config: UgcCacheConfig, tenant: str, sha: str, kind: str) -> bool:
    key = object_key(tenant, sha, kind)
    cached = _status(key)
    if cached is not None:
        return cached
    try:
        hit = _head(_client(config), config, key, sha, kind)
    except Exception:
        hit = False
    _mark(key, hit)
    return hit


async def available(config: UgcCacheConfig, *, tenant: str, sha: str, kind: str) -> bool:
    return await asyncio.to_thread(_has_object, config, tenant, sha, kind)


def _put_if_absent(client, config: UgcCacheConfig, tenant: str, sha: str,
                   kind: str, payload: bytes) -> bool:
    key = object_key(tenant, sha, kind)
    try:
        if _head(client, config, key, sha, kind):
            _mark(key, True)
            return True
    except Exception as exc:
        if not _is_missing(exc):
            return False
    # _head returns False for a missing file or an expired/mismatched file;
    # never overwrite an existing key even if its metadata fails validation.
    try:
        client.head_object(Bucket=config.bucket, Key=key)
        return False
    except Exception as exc:
        if not _is_missing(exc):
            return False
    from hashlib import sha256 as digest
    try:
        # Cloudflare R2 evaluates this precondition atomically. Two concurrent
        # uploads cannot renew the original 30-day object age.
        result = client.put_object(
            Bucket=config.bucket, Key=key, Body=payload, IfNoneMatch="*",
            ContentType="video/mp4" if kind == "playback" else "image/webp",
            CacheControl="private, no-store",
            Metadata={"cam-source-sha256": sha, "cam-payload-sha256": digest(payload).hexdigest()},
        )
    except Exception as exc:
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError) and str(exc.response.get("Error", {}).get("Code")) in (
            "412", "PreconditionFailed",
        ):
            already = _head(client, config, key, sha, kind)
            if already:
                _mark(key, True)
            return already
        return False
    if result.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        return False
    verified = _head(client, config, key, sha, kind)
    if verified:
        _mark(key, True)
    return verified


def _top_level_atoms(mp4: bytes):
    pos = 0
    while pos + 8 <= len(mp4):
        size = int.from_bytes(mp4[pos:pos + 4], "big")
        name = mp4[pos + 4:pos + 8]
        header_size = 8
        if size == 1 and pos + 16 <= len(mp4):
            size = int.from_bytes(mp4[pos + 8:pos + 16], "big")
            header_size = 16
        if size == 0:
            size = len(mp4) - pos
        if size < header_size or pos + size > len(mp4):
            return
        yield name
        pos += size


def has_faststart(mp4: bytes) -> bool:
    found = []
    for atom in _top_level_atoms(mp4):
        if atom in (b"moov", b"mdat"):
            found.append(atom)
            if len(found) >= 2:
                break
    return bool(found) and found[0] == b"moov"


def _prepare_derivatives(mp4: bytes) -> tuple[bytes, dict[str, bytes]]:
    playback = mp4
    posters: dict[str, bytes] = {}
    # Temporary files are removed after each invocation. There is no local
    # persistent cache: on failure, Drive playback remains available.
    with tempfile.TemporaryDirectory(prefix="cam-ugc-", dir=tempfile.gettempdir()) as directory:
        source = Path(directory) / "input.mp4"
        source.write_bytes(mp4)
        playback_path = source
        if not has_faststart(mp4):
            candidate = Path(directory) / "faststart.mp4"
            try:
                remux = subprocess.run(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-i", str(source), "-map", "0", "-c", "copy",
                     "-movflags", "+faststart", "-y", str(candidate)],
                    capture_output=True, timeout=25, check=False,
                )
                if remux.returncode == 0 and candidate.exists() and (
                    2048 <= candidate.stat().st_size <= _MAX_MP4 + 2 * 1024 * 1024
                ):
                    candidate_bytes = candidate.read_bytes()
                    if has_faststart(candidate_bytes):
                        playback = candidate_bytes
                        playback_path = candidate
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        try:
            frame = subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-ss", "0.8", "-i", str(playback_path), "-frames:v", "1",
                 "-vf", "scale=640:-2:flags=lanczos",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, timeout=13, check=False,
            )
            if frame.returncode == 0 and frame.stdout and len(frame.stdout) < 5 * 1024 * 1024:
                with Image.open(io.BytesIO(frame.stdout)) as image:
                    image.load()
                    for size in (320, 640):
                        resized = image.copy()
                        resized.thumbnail((size, size), Image.Resampling.LANCZOS)
                        output = io.BytesIO()
                        resized.save(output, format="WEBP", quality=78, method=4)
                        data = output.getvalue()
                        if 0 < len(data) < _MAX_POSTER:
                            posters[f"poster{size}"] = data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return playback, posters


def _stage(config: UgcCacheConfig, tenant: str, sha: str, mp4: bytes) -> dict[str, bool]:
    if not 2048 <= len(mp4) <= _MAX_MP4 or sha256(mp4).hexdigest() != sha:
        return {}
    try:
        playback, posters = _prepare_derivatives(mp4)
        client = _client(config)
        payloads = {"playback": playback, **posters}
        statuses = {kind: _put_if_absent(client, config, tenant, sha, kind, value)
                    for kind, value in payloads.items()}
        _LOG.info("muse_ugc_cache_ingest playback=%s poster320=%s poster640=%s",
                  statuses.get("playback", False), statuses.get("poster320", False),
                  statuses.get("poster640", False))
        return statuses
    except Exception:
        _LOG.warning("muse_ugc_cache_ingest_failed")
        return {}


async def stage_uploaded(config: UgcCacheConfig | None, *, tenant: str, sha: str,
                         mp4: bytes) -> dict[str, bool]:
    if config is None:
        return {}
    return await asyncio.to_thread(_stage, config, tenant, sha, mp4)


def signed_url(config: UgcCacheConfig, key: str, *, now: int | None = None) -> str | None:
    if not config.edge_enabled or not config.worker_origin or not key.startswith("video-cache/"):
        return None
    now_seconds = int(time.time() if now is None else now)
    expires = now_seconds + 540
    path = "/" + key
    message = f"v1\nread\n{path}\n{expires}".encode("ascii")
    token = base64.urlsafe_b64encode(hmac.new(
        config.signing_secret.encode("utf-8"), message, "sha256").digest()
    ).rstrip(b"=").decode("ascii")
    return f"{config.worker_origin}{path}?v=1&exp={expires}&sig={token}"


async def worker_ready(config: UgcCacheConfig, *, tenant: str, kind: str) -> bool:
    if not config.edge_enabled:
        return False
    name = config.worker_origin + ":" + kind
    with _CACHE_LOCK:
        entry = _WORKER_STATUS.get(name)
        if entry and entry[1] > time.monotonic():
            return entry[0]
    probe = signed_url(config, object_key(tenant, "0" * 64, kind))
    if not probe:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.2, connect=0.7),
                                     follow_redirects=False) as client:
            response = await client.head(probe)
        ready = response.status_code == 404
    except Exception:
        ready = False
    with _CACHE_LOCK:
        _WORKER_STATUS[name] = (ready, time.monotonic() + (60 if ready else 12))
    return ready


def _read_poster(config: UgcCacheConfig, key: str) -> bytes | None:
    try:
        result = _client(config).get_object(Bucket=config.bucket, Key=key)
        body = result["Body"]
        try:
            if (result.get("ContentType") != "image/webp"
                    or int(result.get("ContentLength", 0)) > _MAX_POSTER):
                return None
            value = body.read(_MAX_POSTER + 1)
            return value if 0 < len(value) <= _MAX_POSTER else None
        finally:
            body.close()
    except Exception:
        return None


async def read_poster(config: UgcCacheConfig, *, tenant: str, sha: str,
                      size: int) -> bytes | None:
    if size not in (320, 640):
        return None
    key = object_key(tenant, sha, f"poster{size}")
    return await asyncio.to_thread(_read_poster, config, key)

def _open_video_stream(config: UgcCacheConfig, tenant: str, sha: str,
                       requested_range: str):
    key = object_key(tenant, sha, "playback")
    request_args = {"Bucket": config.bucket, "Key": key}
    if requested_range:
        request_args["Range"] = requested_range
    try:
        result = _client(config).get_object(**request_args)
    except Exception:
        return None
    body = result.get("Body")
    if not body:
        return None
    metadata = result.get("Metadata") or {}
    if (metadata.get("cam-source-sha256") != sha
            or result.get("ContentType") != "video/mp4"):
        body.close()
        return None
    status_code = int(result.get("ResponseMetadata", {}).get("HTTPStatusCode", 200))
    if status_code not in (200, 206) or (requested_range and status_code != 206):
        body.close()
        return None
    headers = {
        "Cache-Control": "private, no-store",
        "Vary": "Cookie, Authorization",
        "Accept-Ranges": "bytes",
        "X-CAM-UGC-Delivery": "r2-proxy",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    }
    if result.get("ContentLength") is not None:
        headers["Content-Length"] = str(result["ContentLength"])
    if status_code == 206:
        content_range = result.get("ContentRange")
        if not content_range:
            body.close()
            return None
        headers["Content-Range"] = str(content_range)
    return status_code, headers, body


async def proxy_video(config: UgcCacheConfig, *, tenant: str, sha: str,
                      requested_range: str):
    """R2 proxy fallback when the signed Worker is not ready, no local MP4."""
    result = await asyncio.to_thread(_open_video_stream, config, tenant, sha, requested_range)
    if result is None:
        return None
    status_code, headers, body = result

    async def stream():
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, 128 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    return status_code, headers, stream()
