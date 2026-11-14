"""Private, bounded thumbnail cache regression tests.

The cache is never shared between tenants and the VPS never persists PNGs.
"""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.modules.etsy_spy import imagegen_drive, image_agent
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache


def test_lru_budget_and_tenant_isolation():
    cache = ThumbnailMemoryCache(max_bytes=12, max_item_bytes=8, ttl_seconds=600)
    a = ("tenant-a", "drive-a", "sha-a")
    b = ("tenant-b", "drive-a", "sha-a")
    c = ("tenant-a", "drive-c", "sha-c")
    cache.put(a, b"a" * 7)
    assert cache.get(b) is None
    cache.put(b, b"b" * 7)
    assert cache.get(a) is None  # eviction under a hard 12-byte budget
    cache.put(c, b"c" * 3)
    assert cache.bytes_used == 10
    cache.put(("big", "asset", "hash"), b"x" * 9)
    assert cache.bytes_used == 10
    assert cache.get(b) == b"b" * 7


def test_expired_entry_is_not_reused(monkeypatch):
    cache = ThumbnailMemoryCache(max_bytes=100, max_item_bytes=100, ttl_seconds=10)
    from app.modules.etsy_spy import thumbnail_memory_cache
    now = {"value": 100.0}
    monkeypatch.setattr(thumbnail_memory_cache, "monotonic", lambda: now["value"])
    key = ("t", "id", "sha")
    cache.put(key, b"old")
    now["value"] = 111
    assert cache.get(key) is None
    assert cache.bytes_used == 0


def test_google_drive_thumbnail_cache_is_per_tenant_and_does_not_cache_original():
    png = b"PNG private thumbnail"
    digest = hashlib.sha256(png).hexdigest()
    remote = "driveprivateimageid123456"
    provider = SimpleNamespace(open_asset=AsyncMock())

    async def open_asset(_request):
        async def body():
            yield png
        return SimpleNamespace(body=body(), close=AsyncMock())

    provider.open_asset.side_effect = open_asset
    cache = ThumbnailMemoryCache(max_bytes=1024, max_item_bytes=1024)
    async def run():
        for tenant in ("tenant-a", "tenant-a", "tenant-b"):
            assert await imagegen_drive.read_design_from_drive(
                tenant_id=tenant, asset_id="job:asset:thumbnail",
                remote_id=remote, sha256=digest, max_bytes=1024,
            ) == png
        assert provider.open_asset.await_count == 2
        assert await imagegen_drive.read_design_from_drive(
            tenant_id="tenant-a", asset_id="job:asset:original",
            remote_id=remote, sha256=digest, max_bytes=1024,
        ) == png
        assert provider.open_asset.await_count == 3

    with patch.object(imagegen_drive, "provider_and_folder", return_value=(provider, "folder")), \
         patch.object(imagegen_drive, "_THUMBNAIL_CACHE", cache):
        asyncio.run(run())


def test_legacy_thumbnail_render_uses_atomic_file_and_reuses_existing(tmp_path):
    from PIL import Image
    original = tmp_path / "legacy.png"
    thumb = tmp_path / "legacy.thumb.png"
    Image.new("RGBA", (900, 700), (22, 55, 120, 200)).save(original)
    image_agent._render_local_design_thumbnail(original, thumb)
    assert thumb.is_file()
    with Image.open(thumb) as rendered:
        assert max(rendered.size) == 192
    before = thumb.stat().st_mtime_ns
    image_agent._render_local_design_thumbnail(original, thumb)
    assert thumb.stat().st_mtime_ns == before
    assert not list(tmp_path.glob("*.pending"))


def test_private_etag_304_still_requires_picked_acl():
    digest = "a" * 64
    principal = SimpleNamespace(active_tenant_id="tenant-a")
    remote = {"thumbnail_sha256": digest, "drive_thumbnail_id": "driveabcdefghij"}
    db = SimpleNamespace(rollback=Mock())
    async def readback(**_kwargs):
        return b"data"
    with patch.object(image_agent, "_private_drive_design", return_value=remote) as acl, \
         patch.object(imagegen_drive, "read_design_from_drive",
                      new_callable=AsyncMock, side_effect=readback) as reader:
        response = asyncio.run(image_agent.view_design_thumbnail(
            "job-id", "asset-id", principal=principal, db=db,
            if_none_match=f'"{digest}"',
        ))
        assert response.status_code == 304
        assert response.headers["etag"] == f'"{digest}"'
        assert response.headers["cache-control"].startswith("private,")
        acl.assert_called_once()
        reader.assert_not_awaited()
        db.rollback.assert_not_called()

        other = asyncio.run(image_agent.view_design_thumbnail(
            "job-id", "asset-id", principal=principal, db=db,
            if_none_match='"different"',
        ))
        assert other.status_code == 200
        reader.assert_awaited_once()
        db.rollback.assert_called_once()
