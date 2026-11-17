"""WebP preview conversion, cache isolation, bounded concurrency, and fallback."""
from __future__ import annotations

import asyncio
import hashlib
import io
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image

from app.modules.etsy_spy import imagegen_drive
from app.modules.etsy_spy import listing_image_preview as preview
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache


def png_bytes(*, width=1254, height=940, mode="RGBA") -> bytes:
    img = Image.new(mode, (width, height),
        (60, 110, 170, 140) if mode == "RGBA" else (60, 110, 170))
    data = io.BytesIO()
    img.save(data, format="PNG")
    return data.getvalue()


def test_render_webp_is_bounded_and_keeps_alpha():
    raw = png_bytes()
    result = preview.render_listing_preview(raw)
    assert len(result) > 40
    assert len(result) < 300_000
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "WEBP"
        assert max(image.size) == 720
        assert image.mode == "RGBA"
        assert image.getpixel((100, 100))[3] < 255


def test_bad_sources_fail_without_caching():
    with pytest.raises(HTTPException) as exc:
        preview.render_listing_preview(b"not an image")
    assert exc.value.status_code == 502
    huge = png_bytes(width=4500, height=4500)
    with pytest.raises(HTTPException) as exc:
        preview.render_listing_preview(huge)
    assert exc.value.status_code == 502


def test_preview_cache_isolation_and_singleflight(monkeypatch):
    source = png_bytes(width=640, height=480, mode="RGB")
    digest = hashlib.sha256(source).hexdigest()
    original = AsyncMock(return_value=source)
    monkeypatch.setattr(imagegen_drive, "read_design_from_drive", original)
    monkeypatch.setattr(preview, "_PREVIEW_CACHE",
        ThumbnailMemoryCache(max_bytes=4*1024*1024,
                             max_item_bytes=2*1024*1024))
    monkeypatch.setattr(preview, "_FLIGHTS", {})

    async def run():
        args = dict(pack_id="sp-"+"b"*40, slot=1,
            remote_id="testFileStored1234567890", sha256=digest)
        results = await asyncio.gather(*[
            preview.get_listing_preview(tenant_id="tenant-a", **args)
            for _ in range(6)
        ])
        assert all(result == results[0] for result in results)
        assert original.await_count == 1
        assert await preview.get_listing_preview(tenant_id="tenant-a",
                                                 **args) == results[0]
        assert original.await_count == 1
        assert await preview.get_listing_preview(tenant_id="tenant-b",
                                                 **args) == results[0]
        assert original.await_count == 2
        with Image.open(io.BytesIO(results[0])) as image:
            assert image.format == "WEBP"

    asyncio.run(run())


def test_bounded_concurrent_drive_downloads(monkeypatch):
    raw = png_bytes(width=300, height=300, mode="RGB")
    digest = hashlib.sha256(raw).hexdigest()
    inflight = [0, 0]

    async def fetch(**_):
        inflight[0] += 1
        inflight[1] = max(inflight[1], inflight[0])
        try:
            await asyncio.sleep(0.025)
            return raw
        finally:
            inflight[0] -= 1

    monkeypatch.setattr(imagegen_drive, "read_design_from_drive", fetch)
    monkeypatch.setattr(preview, "_PREVIEW_CACHE",
        ThumbnailMemoryCache(max_bytes=4*1024*1024,
                             max_item_bytes=2*1024*1024))
    monkeypatch.setattr(preview, "_FLIGHTS", {})

    async def run():
        result = await asyncio.gather(*[
            preview.get_listing_preview(
                tenant_id="tenant-a", pack_id="sp-"+f"{slot:040x}", slot=slot,
                remote_id=f"testObject{slot:030x}", sha256=digest)
            for slot in range(1, 11)
        ])
        assert len(result) == 10
        assert 1 < inflight[1] <= preview._MAX_CONCURRENT_PREVIEWS

    asyncio.run(run())
