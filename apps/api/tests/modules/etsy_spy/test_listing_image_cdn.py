"""Fail-closed private R2/Worker delivery; no Cloudflare credentials required."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.modules.etsy_spy import listing_image_cdn as cdn


@pytest.fixture(autouse=True)
def reset_cdn_probe_cache():
    with cdn._STATUS_LOCK:
        cdn._OBJECT_STATUS.clear()
        cdn._STAGING.clear()
    yield
    with cdn._STATUS_LOCK:
        cdn._OBJECT_STATUS.clear()
        cdn._STAGING.clear()


def request_kwargs(*, quality="preview", tenant="boostgame"):
    return dict(tenant_id=tenant, pack_id="sp-"+"a"*40, slot=1,
                quality=quality, sha256="c"*64)


def test_feature_flag_is_off_and_invalid_config_stays_off(monkeypatch):
    monkeypatch.delenv("CAM_ETSY_IMAGE_CDN_ENABLED", raising=False)
    monkeypatch.delenv("CAM_ETSY_IMAGE_CDN_CANARY_TENANTS", raising=False)
    assert cdn.cdn_config(tenant_id="boostgame") is None
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_ENABLED", "true")
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_WORKER_URL", "http://localhost")
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_HMAC_HEX", "ff"*32)
    assert cdn.cdn_config(tenant_id="boostgame") is None
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_WORKER_URL",
                       "https://example.workers.dev")
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_HMAC_HEX", "bad")
    assert cdn.cdn_config(tenant_id="boostgame") is None
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_HMAC_HEX", "11"*32)
    # Explicit tenant allowlist is a mandatory second kill switch.
    assert cdn.cdn_config(tenant_id="boostgame") is None
    monkeypatch.setenv("CAM_ETSY_IMAGE_CDN_CANARY_TENANTS", "boostgame")
    assert cdn.cdn_config(tenant_id="boostgame") is not None
    assert cdn.cdn_config(tenant_id="another-tenant") is None


def test_cdn_keys_are_tenant_partitioned_and_disallow_native_original():
    config=cdn.CdnConfig(base_url="https://example.workers.dev",key=bytes.fromhex("11"*32))
    a=cdn.object_key(config,**request_kwargs())
    b=cdn.object_key(config,**request_kwargs(tenant="second"))
    assert a!=b
    assert a.endswith("/01/preview/"+"c"*64+"-w720v1.webp")
    assert "boostgame" not in a
    with pytest.raises(ValueError):
        cdn.object_key(config,**request_kwargs(quality="original"))
    with pytest.raises(ValueError):
        cdn.object_key(config,**{**request_kwargs(), "pack_id":"../../../etc/passwd"})
    read, _=cdn.ticket(config,"GET",a,now=1000)
    head, _=cdn.ticket(config,"HEAD",a,now=1000)
    assert read != head
    parts=urlsplit(read)
    params=parse_qs(parts.query)
    assert params["exp"]==["1045"]
    payload=f"GET\n{parts.path}\n1045"
    expected=hmac.new(config.key,payload.encode(),hashlib.sha256).hexdigest()
    assert params["sig"]==[expected]


def test_worker_unavailable_falls_back_to_authenticated_cam(monkeypatch):
    config=cdn.CdnConfig(base_url="https://example.workers.dev",key=bytes.fromhex("11"*32))
    # Missing bucket or DNS => no redirect, and never expose the source PNG URL.
    async def missing():
        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self,*_): pass
            async def head(self, _):
                return httpx.Response(404)
        monkeypatch.setattr(cdn.httpx,"AsyncClient",lambda **_:FakeClient())
        assert await cdn.available_cdn_url(config,**request_kwargs()) is None
    asyncio.run(missing())


def test_ready_derivative_issues_signed_get_after_probe(monkeypatch):
    config=cdn.CdnConfig(base_url="https://example.workers.dev",key=bytes.fromhex("11"*32))
    async def ready():
        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self,*_): pass
            async def head(self, _):
                return httpx.Response(200)
        monkeypatch.setattr(cdn.httpx,"AsyncClient",lambda **_:FakeClient())
        result=await cdn.available_cdn_url(config,**request_kwargs())
        assert result is not None
        assert "sig=" in result
        assert "/preview/" in result
    asyncio.run(ready())

def test_negative_probe_cache_promotes_to_cdn_after_bounded_upload(monkeypatch):
    config = cdn.CdnConfig(
        base_url="https://example.workers.dev", key=bytes.fromhex("11" * 32))
    operations = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def head(self, _):
            operations.append("head")
            return httpx.Response(404)

        async def put(self, _, *, content, headers):
            assert headers["Content-Type"] == "image/webp"
            assert content == b"private-preview"
            operations.append("put")
            return httpx.Response(204)

    monkeypatch.setattr(cdn.httpx, "AsyncClient", lambda **_: FakeClient())

    async def run():
        args = request_kwargs()
        assert await cdn.available_cdn_url(config, **args) is None
        assert await cdn.available_cdn_url(config, **args) is None
        assert operations == ["head"], "A 404 must be negatively cached"
        await cdn.stage_derivative(
            config, **args, data=b"private-preview")
        assert operations == ["head", "put"]
        redirected = await cdn.available_cdn_url(config, **args)
        assert redirected is not None and "sig=" in redirected
        assert operations == ["head", "put"], "Successful PUT promotes to a cache hit"
        await cdn.stage_derivative(
            config, **args, data=b"private-preview")
        assert operations == ["head", "put"], "Known object must not upload again"

    asyncio.run(run())


def test_staging_fails_open_and_retries_after_worker_error(monkeypatch):
    config = cdn.CdnConfig(
        base_url="https://example.workers.dev", key=bytes.fromhex("11" * 32))
    operations = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def put(self, _, **_kw):
            operations.append("put")
            return httpx.Response(503)

    monkeypatch.setattr(cdn.httpx, "AsyncClient", lambda **_: FakeClient())

    async def run():
        args = request_kwargs()
        # Upload is best effort: a 5xx may never fail a normal image response.
        await cdn.stage_derivative(config, **args, data=b"private-preview")
        assert operations == ["put"]
        key = cdn.object_key(config, **args)
        assert cdn._cached_status(cdn._status_key(config, key)) is False
        # Test cache expiration without sleeping or hitting Cloudflare.
        cdn._remember_status(cdn._status_key(config, key), available=False, ttl=-1)
        assert cdn._cached_status(cdn._status_key(config, key)) is None

    asyncio.run(run())


def test_stage_capacity_fails_open_when_limit_reached(monkeypatch):
    from threading import BoundedSemaphore

    config = cdn.CdnConfig(
        base_url="https://example.workers.dev", key=bytes.fromhex("11" * 32))
    monkeypatch.setattr(cdn, "_STAGE_SLOTS", BoundedSemaphore(0))

    async def run():
        await cdn.stage_derivative(
            config, **request_kwargs(), data=b"private-preview")
        with cdn._STATUS_LOCK:
            assert not cdn._STAGING

    asyncio.run(run())
