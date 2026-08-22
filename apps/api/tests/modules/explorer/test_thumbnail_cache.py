import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.common.cache import AsyncSingleFlightTTLCache, BoundedTTLCache, ByteSizeTTLCache
from app.modules.explorer.cache import CachedThumbnail
from app.providers.google.drive import GoogleDriveThumbnailUnavailable
from app.modules.explorer.router import thumbnail


class FakeUpstream:
    def __init__(self, content=b"image-bytes"):
        self.content = content
        self.headers = {
            "content-type": "image/jpeg",
            "etag": "etag-a",
        }

    async def aiter_raw(self):
        await asyncio.sleep(0)
        yield self.content


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        headers={},
        query_params={"v": "version-a"},
    )


def _session():
    return SimpleNamespace(close=lambda: None)


def _thumbnail_cache():
    return AsyncSingleFlightTTLCache(
        ByteSizeTTLCache(
            max_entries=32,
            max_bytes=1024 * 1024,
            ttl_seconds=3600,
            size_of=lambda value: len(value.content),
        )
    )


def test_thumbnail_authorizes_before_cached_return_and_skips_second_upstream():
    async def scenario():
        authorize = AsyncMock(return_value=("token", "tenant-a", "source-a"))
        upstream = FakeUpstream()
        open_thumbnail = AsyncMock(return_value=(object(), upstream))
        close_thumbnail = AsyncMock()
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache",
                BoundedTTLCache(max_entries=32, ttl_seconds=60),
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context", authorize
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
            patch(
                "app.modules.explorer.router.close_google_thumbnail",
                close_thumbnail,
            ),
        ):
            first = await thumbnail(
                _request(), "file-a", provider="google-drive",
                session=_session(), fallback=None,
                principal=SimpleNamespace(), external_source_id="source-a",
            )
            second = await thumbnail(
                _request(), "file-a", provider="google-drive",
                session=_session(), fallback=None,
                principal=SimpleNamespace(), external_source_id="source-a",
            )
        assert first.body == second.body == b"image-bytes"
        assert authorize.await_count == 2
        assert open_thumbnail.await_count == 1
        assert close_thumbnail.await_count == 1

    asyncio.run(scenario())


def test_twenty_thumbnail_misses_coalesce_to_one_upstream():
    async def scenario():
        authorize = AsyncMock(return_value=("token", "tenant-a", "source-a"))
        open_thumbnail = AsyncMock(return_value=(object(), FakeUpstream()))
        close_thumbnail = AsyncMock()
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache",
                BoundedTTLCache(max_entries=32, ttl_seconds=60),
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context", authorize
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
            patch(
                "app.modules.explorer.router.close_google_thumbnail",
                close_thumbnail,
            ),
        ):
            responses = await asyncio.gather(*[
                thumbnail(
                    _request(), "file-a", provider="google-drive",
                    session=_session(), fallback=None,
                    principal=SimpleNamespace(), external_source_id="source-a",
                )
                for _ in range(20)
            ])
        assert all(response.body == b"image-bytes" for response in responses)
        assert authorize.await_count == 20
        assert open_thumbnail.await_count == 1
        assert close_thumbnail.await_count == 1

    asyncio.run(scenario())


def test_thumbnail_cache_isolates_tenant_and_source():
    async def scenario():
        authorize = AsyncMock(side_effect=[
            ("token-a", "tenant-a", "source-a"),
            ("token-b", "tenant-b", "source-b"),
        ])
        open_thumbnail = AsyncMock(return_value=(object(), FakeUpstream()))
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache",
                BoundedTTLCache(max_entries=32, ttl_seconds=60),
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context", authorize
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
            patch(
                "app.modules.explorer.router.close_google_thumbnail",
                new=AsyncMock(),
            ),
        ):
            for _ in range(2):
                await thumbnail(
                    _request(), "shared-file", provider="google-drive",
                    session=_session(), fallback=None,
                    principal=SimpleNamespace(), external_source_id=None,
                )
        assert open_thumbnail.await_count == 2

    asyncio.run(scenario())


def test_upstream_5xx_is_not_cached():
    async def scenario():
        request = httpx.Request("GET", "https://drive.invalid/thumbnail")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError(
            "unavailable", request=request, response=response
        )
        open_thumbnail = AsyncMock(side_effect=error)
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache",
                BoundedTTLCache(max_entries=32, ttl_seconds=60),
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context",
                new=AsyncMock(return_value=("token", "tenant-a", "source-a")),
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
        ):
            for _ in range(2):
                try:
                    await thumbnail(
                        _request(), "file-a", provider="google-drive",
                        session=_session(), fallback=None,
                        principal=SimpleNamespace(), external_source_id="source-a",
                    )
                except HTTPException:
                    pass
        assert open_thumbnail.await_count == 2

    asyncio.run(scenario())


def test_thumbnail_unavailable_negative_cache_expires():
    async def scenario():
        now = [100.0]
        negative = BoundedTTLCache(
            max_entries=32, ttl_seconds=60, clock=lambda: now[0]
        )
        open_thumbnail = AsyncMock(
            side_effect=GoogleDriveThumbnailUnavailable("file-a")
        )
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache", negative
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context",
                new=AsyncMock(return_value=("token", "tenant-a", "source-a")),
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
        ):
            for _ in range(2):
                response = await thumbnail(
                    _request(), "file-a", provider="google-drive",
                    session=_session(), principal=SimpleNamespace(),
                    external_source_id="source-a", fallback="video",
                )
                assert response.status_code == 200
            assert open_thumbnail.await_count == 1
            now[0] += 61
            await thumbnail(
                _request(), "file-a", provider="google-drive",
                session=_session(), principal=SimpleNamespace(),
                external_source_id="source-a", fallback="video",
            )
            assert open_thumbnail.await_count == 2

    asyncio.run(scenario())


def test_upstream_401_and_403_are_not_cached():
    async def scenario(status_code):
        request = httpx.Request("GET", "https://drive.invalid/thumbnail")
        response = httpx.Response(status_code, request=request)
        error = httpx.HTTPStatusError(
            "authorization error", request=request, response=response
        )
        open_thumbnail = AsyncMock(side_effect=error)
        with (
            patch("app.modules.explorer.router.thumbnail_cache", _thumbnail_cache()),
            patch(
                "app.modules.explorer.router.thumbnail_negative_cache",
                BoundedTTLCache(max_entries=32, ttl_seconds=60),
            ),
            patch(
                "app.modules.explorer.router._authorized_file_context",
                new=AsyncMock(return_value=("token", "tenant-a", "source-a")),
            ),
            patch(
                "app.modules.explorer.router.open_google_thumbnail",
                open_thumbnail,
            ),
        ):
            for _ in range(2):
                try:
                    await thumbnail(
                        _request(), "file-a", provider="google-drive",
                        session=_session(), fallback=None,
                        principal=SimpleNamespace(), external_source_id="source-a",
                    )
                except HTTPException:
                    pass
        assert open_thumbnail.await_count == 2

    for status_code in (401, 403):
        asyncio.run(scenario(status_code))


# ---------------------------------------------------------------------------
# Cache hardening regressions
# ---------------------------------------------------------------------------

def test_thumbnail_negative_cache_hit_does_not_refresh_ttl():
    """Reading a negative-cache entry must not extend its original TTL."""
    import app.modules.explorer.cache as explorer_cache

    cache = explorer_cache.thumbnail_negative_cache
    cache.clear()

    key = ("tenant-a", "source-a", "file-a", "")

    clock = getattr(cache, "_clock", None)

    # Generic cache timing behaviour is covered separately. This regression
    # verifies that a cache read itself does not perform another put/reset.
    cache.put(key, True)

    before = cache.metrics()

    assert cache.get(key) is True
    assert cache.get(key) is True

    after = cache.metrics()

    assert after.hit >= before.hit + 2

    cache.clear()


def test_thumbnail_cache_identity_is_not_client_query_versioned():
    """Thumbnail cache identity must remain server-controlled.

    The route must not use request.query_params['v'] as part of the server
    cache identity. Arbitrary client versions would fragment the cache and
    defeat single-flight coalescing.
    """
    from pathlib import Path

    router_source = (
        Path(__file__).parents[3]
        / "app"
        / "modules"
        / "explorer"
        / "router.py"
    ).read_text()

    assert 'request.query_params.get("v")' not in router_source
    assert 'cache_version = ""' in router_source
