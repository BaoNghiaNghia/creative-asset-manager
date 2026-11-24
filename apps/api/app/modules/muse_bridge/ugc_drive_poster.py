"""Private, bounded Drive poster fallback. Never reads MP4 bytes on GET."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import OrderedDict
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

import httpx
from PIL import Image

from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache

_LOG = logging.getLogger(__name__)
_CACHE = ThumbnailMemoryCache(max_bytes=16 * 1024 * 1024, ttl_seconds=900)
_MAX_BYTES = 2 * 1024 * 1024
_SLOTS = asyncio.Semaphore(3)
_ID = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_LISTING = re.compile(r"[0-9]{5,20}\Z")
_MISSES: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_MISSES_LOCK = Lock()


def _recent_miss(key: tuple[str, str, str]) -> bool:
    with _MISSES_LOCK:
        expires = _MISSES.get(key, 0)
        if expires <= monotonic():
            _MISSES.pop(key, None)
            return False
        return True


def _remember_miss(key: tuple[str, str, str]) -> None:
    with _MISSES_LOCK:
        _MISSES[key] = monotonic() + 60
        _MISSES.move_to_end(key)
        while len(_MISSES) > 1024:
            _MISSES.popitem(last=False)


def safe_thumbnail_url(value: object) -> str | None:
    """A Google bearer token must never be sent to a third-party host."""
    if (not isinstance(value, str) or len(value) > 4096 or
            not value.isascii() or any(ord(ch) <= 32 or ord(ch) == 127 for ch in value)):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not (
            host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
        ) or parsed.username or parsed.password or parsed.port is not None
                or parsed.fragment or not parsed.path):
            return None
    except ValueError:
        return None
    return value


def encode_poster(payload: bytes, size: int) -> bytes | None:
    if size not in (320, 640) or not 0 < len(payload) <= _MAX_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(payload)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP") or img.width * img.height > 16_000_000:
                return None
            img.load()
            frame = img.convert("RGB")
            frame.thumbnail((size, size), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            frame.save(output, format="WEBP", quality=78, method=4)
            value = output.getvalue()
            return value if 0 < len(value) <= 512 * 1024 else None
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


async def read_drive_poster(*, tenant: str, listing_id: str, artwork_sha: str,
                            sha: str, aspect: str, remote_id: str,
                            folder_id: str, size: int) -> bytes | None:
    """Caller MUST authorize active tenant and current Picked receipt first.
    Google metadata supplies the real video preview; the MP4 is never read.
    This fallback has no R2 PUT, Drive write or database mutation.
    """
    if (size not in (320, 640) or aspect not in ("vertical", "horizontal") or
            not _TENANT.fullmatch(tenant) or not _LISTING.fullmatch(listing_id) or
            not _SHA.fullmatch(artwork_sha) or not _SHA.fullmatch(sha) or
            not _ID.fullmatch(remote_id) or not _ID.fullmatch(folder_id)):
        return None
    key = (tenant, ":".join((listing_id, artwork_sha, aspect, folder_id, remote_id, sha)), str(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    if _recent_miss(key):
        return None
    from app.modules.etsy_spy.imagegen_drive import provider_and_folder
    from app.modules.etsy_spy.listing_drive_folders import (
        ROOT_FOLDER_ID, verify_listing_file_provenance,
    )
    async with _SLOTS:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        if _recent_miss(key):
            return None
        try:
            provider, root = provider_and_folder()
            if root != ROOT_FOLDER_ID:
                return None
            await verify_listing_file_provenance(
                provider=provider, remote_file_id=remote_id, folder_id=folder_id,
                tenant_id=tenant, asset_id=f"muse-ugc:{listing_id}:{artwork_sha}:{aspect}",
                sha256=sha,
            )
            token = await provider.get_access_token()
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4), follow_redirects=False,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                metadata = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{remote_id}",
                    params={"supportsAllDrives": "true", "fields": "thumbnailLink"},
                )
                metadata.raise_for_status()
                url = safe_thumbnail_url(metadata.json().get("thumbnailLink"))
                if not url:
                    _remember_miss(key)
                    return None
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    if response.headers.get("content-type", "").split(";")[0].lower() not in (
                        "image/jpeg", "image/png", "image/webp"
                    ):
                        return None
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > _MAX_BYTES:
                            return None
            payload = await asyncio.to_thread(encode_poster, bytes(data), size)
            if payload:
                _CACHE.put(key, payload)
            else:
                _remember_miss(key)
            return payload
        except Exception:
            # A private media failure is never a reason to disclose tokens or
            # bypass receipt authorization. UI can still open verified video.
            _remember_miss(key)
            _LOG.info("muse_ugc_drive_poster_unavailable")
            return None
