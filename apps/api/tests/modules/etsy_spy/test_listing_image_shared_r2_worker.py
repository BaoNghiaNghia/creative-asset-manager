"""Worker rollout checks: false/expired auth routes do not redirect browser."""
import asyncio
import httpx
from app.modules.etsy_spy import listing_image_shared_r2 as cache

SECRET = "Strong-VideoCache-SharedWorker-Secret-" + "A1b2C3" * 10
CONFIG = cache.ExistingR2Config(
    bucket="creative-asset-video-cache",
    endpoint="https://account.r2.cloudflarestorage.com",
    access_key="fake", secret_key="fake",
    signing_secret=SECRET,
    worker_origin="https://cam-r2-original-video.example.workers.dev",
    worker_enabled=True,
)

def test_worker_readiness_fails_safe_until_signed_image_route_is_deployed(monkeypatch):
    monkeypatch.setattr(cache, "_WORKER_STATUS", {})
    probes = []
    response_status = [403]
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def head(self, url):
            assert "/etsy-images/v1/" in url
            assert "?v=1&exp=" in url and "&sig=" in url
            probes.append(url)
            return httpx.Response(response_status[0])
    monkeypatch.setattr(cache.httpx, "AsyncClient", lambda **_: FakeClient())
    async def run():
        assert await cache.worker_ready(CONFIG) is False
        assert await cache.worker_ready(CONFIG) is False
        assert len(probes) == 1
        response_status[0] = 404
        cache._WORKER_STATUS.clear()
        assert await cache.worker_ready(CONFIG) is True
        assert await cache.worker_ready(CONFIG) is True
        assert len(probes) == 2
        assert await cache.worker_ready(
            cache.ExistingR2Config(**{**CONFIG.__dict__, "worker_enabled":False})) is False
    asyncio.run(run())
