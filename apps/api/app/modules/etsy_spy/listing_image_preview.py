"""Bounded, private, memory-only 720px WebP preview of saved Listing Images.

Original PNG stays in Google Drive, never on the VPS filesystem and never in
an unprotected CDN. Callers must check the principal, tenant and Picked ACL
before this helper. Only the verified, derived preview is cached.
"""
from __future__ import annotations

import asyncio
import io
from threading import BoundedSemaphore, Lock
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from . import imagegen_drive
from .request_metrics import ETSY_PERFORMANCE
from .thumbnail_memory_cache import ThumbnailMemoryCache

PREVIEW_VERSION = "webp720-v1"
PREVIEW_SIZE = 720
_MAX_PIXELS = 16_000_000
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024
_PREVIEW_CACHE = ThumbnailMemoryCache(
    max_bytes=32 * 1024 * 1024,
    max_item_bytes=_MAX_PREVIEW_BYTES,
    ttl_seconds=900,
)
_RENDER_SLOTS = BoundedSemaphore(2)
_FLIGHT_LOCK = Lock()
_FLIGHTS: dict[tuple[asyncio.AbstractEventLoop, str, str, str], asyncio.Task[bytes]] = {}
_MAX_FLIGHTS = 128
# Bound the entire Drive download + render pipeline, not only CPU encoding.
# One semaphore per event loop avoids accidental cross-loop binding in tests.
_GATE_LOCK = Lock()
_LOOP_GATES: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_MAX_CONCURRENT_PREVIEWS = 3


def _preview_gate(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    with _GATE_LOCK:
        gate = _LOOP_GATES.get(loop)
        if gate is None:
            gate = asyncio.Semaphore(_MAX_CONCURRENT_PREVIEWS)
            _LOOP_GATES[loop] = gate
        return gate


def render_listing_preview(source: bytes) -> bytes:
    """Decode strictly bounded PNG and make a max 720px WebP without a disk write."""
    with _RENDER_SLOTS:
        try:
            with Image.open(io.BytesIO(source)) as image:
                width, height = image.size
                if (image.format != "PNG" or width < 1 or height < 1
                        or width * height > _MAX_PIXELS):
                    raise ValueError("Invalid listing preview source")
                image.load()
                has_alpha = image.mode in ("RGBA", "LA", "PA") or (
                    image.mode == "P" and "transparency" in image.info)
                thumb = image.convert("RGBA" if has_alpha else "RGB")
                thumb.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                thumb.save(output, format="WEBP", quality=80, method=4)
                value = output.getvalue()
                if not 0 < len(value) <= _MAX_PREVIEW_BYTES:
                    raise ValueError("Listing preview exceeds size budget")
                return value
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise HTTPException(502, "Listing Image preview unavailable") from exc


async def get_listing_preview(
    *, tenant_id: str, pack_id: str, slot: int, remote_id: str,
    sha256: str,
) -> bytes:
    """Coalesce concurrent misses by tenant and verified original checksum."""
    key = (tenant_id, remote_id, sha256 + ":" + PREVIEW_VERSION)
    cached = _PREVIEW_CACHE.get(key)
    if cached is not None:
        ETSY_PERFORMANCE.cache(True, label="listing_preview")
        return cached

    async def generate() -> bytes:
        existing = _PREVIEW_CACHE.get(key)
        if existing is not None:
            return existing
        async with _preview_gate(loop):
            # A queued request may find the preview was populated while waiting.
            cached_after_wait = _PREVIEW_CACHE.get(key)
            if cached_after_wait is not None:
                return cached_after_wait
            verified_png = await imagegen_drive.read_design_from_drive(
                tenant_id=tenant_id,
                asset_id=f"{pack_id}:listing-{slot:02d}:original",
                remote_id=remote_id, sha256=sha256,
                max_bytes=80 * 1024 * 1024,
            )
            result = await asyncio.to_thread(render_listing_preview, verified_png)
            _PREVIEW_CACHE.put(key, result)
            return result

    loop = asyncio.get_running_loop()
    flight_key = (loop, *key)
    with _FLIGHT_LOCK:
        task = _FLIGHTS.get(flight_key)
        joined = task is not None and not task.done()
        if not joined:
            task = asyncio.create_task(generate())
            if len(_FLIGHTS) < _MAX_FLIGHTS:
                _FLIGHTS[flight_key] = task

                def forget(completed: asyncio.Task[bytes]) -> None:
                    with _FLIGHT_LOCK:
                        if _FLIGHTS.get(flight_key) is completed:
                            _FLIGHTS.pop(flight_key, None)

                task.add_done_callback(forget)
    ETSY_PERFORMANCE.cache(False, coalesced=joined, label="listing_preview")
    return await asyncio.shield(task)
