"""No-PII Etsy Spy p95/5xx telemetry and bounded Drive coalescing."""
import asyncio
import hashlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.modules.etsy_spy import imagegen_drive
from app.modules.etsy_spy.request_metrics import EtsyPerformanceMeter, route_label
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache


def test_only_allowlisted_route_names_are_measured():
    assert route_label("GET", "/api/v1/etsy-spy/summary") == "summary"
    assert route_label("GET", "/api/v1/etsy-spy/listings?page=1") is None
    assert route_label("GET", "/api/v1/etsy-spy/image-agents/designs/job-123/asset-123/thumbnail") == "design_thumbnail"
    assert route_label("GET", "/api/v1/etsy-spy/image-agents/designs/job-123/asset-123/original") is None
    assert route_label("POST", "/api/v1/etsy-spy/summary") is None
    assert route_label("GET", "/api/v1/private/other") is None


def test_five_minute_aggregate_has_p95_and_no_untrusted_identifiers(caplog):
    meter = EtsyPerformanceMeter()
    with caplog.at_level(logging.INFO, logger="app.etsy_spy_performance"):
        for i in range(19):
            meter.record("listings", status_code=200, duration_ms=55, now=30)
        meter.record("listings", status_code=502, duration_ms=1100, now=35)
        meter.cache(True, now=40)
        meter.cache(False, coalesced=True, now=41)
        meter.record("summary", status_code=200, duration_ms=65, now=301)
    text = caplog.text
    assert "route=listings requests=20 errors_5xx=1 errors_502=1" in text
    assert "p95_le_ms=100" in text
    assert "cache_hit=1 cache_miss=1 coalesced=1" in text
    assert "tenant-" not in text
    assert "asset-" not in text


def test_24h_report_aggregates_histograms_without_asset_ids():
    import subprocess
    import sys
    import time
    from pathlib import Path

    script = Path(__file__).resolve().parents[5] / "scripts" / "cam-etsy-perf-report.py"
    histogram = ",".join(map(str, [0, 19, 0, 0, 0, 1, 0, 0, 0, 0]))
    line = (
        "etsy_perf_window window_start=" + str(int(time.time()) - 360) +
        " window_s=300 route=listings requests=20 errors_5xx=1 "
        "errors_502=1 p95_le_ms=100 cache_hit=0 cache_miss=0 "
        "coalesced=0 histogram=" + histogram + "\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--hours", "24"], input=line,
        text=True, capture_output=True, check=True,
    )
    assert "listings" in result.stdout
    assert "20" in result.stdout
    assert "Nginx/proxy-generated 502" in result.stdout
    assert "tenant" not in result.stdout


def test_singleflight_coalesces_concurrent_private_thumbnail_reads():
    png = b"private preview bytes" * 12
    digest = hashlib.sha256(png).hexdigest()
    started = asyncio.Event()
    release = asyncio.Event()
    provider = SimpleNamespace(open_asset=AsyncMock())

    async def open_asset(_request):
        started.set()
        await release.wait()

        async def body():
            yield png[:20]
            yield png[20:]

        return SimpleNamespace(body=body(), close=AsyncMock())

    provider.open_asset.side_effect = open_asset
    cache = ThumbnailMemoryCache(max_bytes=1024, max_item_bytes=1024)
    async def read(tenant="tenant-1", kind="thumbnail"):
        return await imagegen_drive.read_design_from_drive(
            tenant_id=tenant, asset_id="job:asset:" + kind,
            remote_id="privateDriveObject00001", sha256=digest,
            max_bytes=1024,
        )

    async def execute():
        first = asyncio.create_task(read())
        await started.wait()
        second = asyncio.create_task(read())
        await asyncio.sleep(0)
        assert provider.open_asset.await_count == 1
        release.set()
        assert await asyncio.gather(first, second) == [png, png]
        assert provider.open_asset.await_count == 1
        assert await read() == png
        assert provider.open_asset.await_count == 1
        assert await read("tenant-2") == png
        assert provider.open_asset.await_count == 2
        assert await read("tenant-1", "original") == png
        assert provider.open_asset.await_count == 3

    with patch.object(imagegen_drive, "provider_and_folder", return_value=(provider, "folder")), \
         patch.object(imagegen_drive, "_THUMBNAIL_CACHE", cache):
        asyncio.run(execute())


def test_failed_thumbnail_fetch_not_cached_and_is_retried_later():
    png = b"correct"
    digest = hashlib.sha256(png).hexdigest()
    provider = SimpleNamespace(open_asset=AsyncMock())
    responses = [b"wrong", png]

    async def open_asset(_request):
        payload = responses.pop(0)

        async def body():
            yield payload

        return SimpleNamespace(body=body(), close=AsyncMock())

    provider.open_asset.side_effect = open_asset
    cache = ThumbnailMemoryCache(max_bytes=1024, max_item_bytes=1024)

    async def run():
        params = dict(
            tenant_id="tenant-1", asset_id="job:asset:thumbnail",
            remote_id="privateDriveObject00001", sha256=digest,
            max_bytes=1024,
        )
        with pytest.raises(HTTPException) as error:
            await imagegen_drive.read_design_from_drive(**params)
        assert error.value.status_code == 502
        assert await imagegen_drive.read_design_from_drive(**params) == png
        assert provider.open_asset.await_count == 2

    with patch.object(imagegen_drive, "provider_and_folder", return_value=(provider, "folder")), \
         patch.object(imagegen_drive, "_THUMBNAIL_CACHE", cache):
        asyncio.run(run())
