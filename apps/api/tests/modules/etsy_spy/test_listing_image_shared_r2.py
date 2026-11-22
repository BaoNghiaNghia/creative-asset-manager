"""Etsy Spy reuses the existing private R2 video bucket without sharing video keys."""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from hashlib import sha256
import hmac
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

from botocore.exceptions import ClientError
import pytest

from app.modules.etsy_spy import listing_image_shared_r2 as cache
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache


SECRET = "a-secure-ExistingVideo-Worker-secret-" + "Ab3" * 19
CONFIG = cache.ExistingR2Config(
    bucket="creative-asset-video-cache",
    endpoint="https://example.r2.cloudflarestorage.com",
    access_key="fake-access", secret_key="fake-secret",
    signing_secret=SECRET,
    worker_origin="https://cam-r2-original-video.example.workers.dev",
)


class Secret:
    def __init__(self, value: str):
        self.value = value

    def get_secret_value(self):
        return self.value


class MemoryS3:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}
        self.puts = 0
        self.reads = 0

    def put_object(self, *, Bucket: str, Key: str, Body: bytes,
                   ContentType: str, Metadata: dict, CacheControl: str):
        assert Bucket == CONFIG.bucket
        assert Key.startswith("etsy-images/v1/")
        assert not Key.startswith("video-cache/")
        assert CacheControl == "private, no-store"
        self.objects[Key] = (Body, ContentType, Metadata)
        self.puts += 1
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, *, Bucket: str, Key: str):
        assert Bucket == CONFIG.bucket
        self.reads += 1
        if Key not in self.objects:
            raise ClientError({
                "Error": {"Code": "NoSuchKey", "Message": "not found"},
                "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
        data, mime, metadata = self.objects[Key]
        return {
            "Body": BytesIO(data), "ContentLength": len(data),
            "ContentType": mime, "Metadata": metadata,
        }


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(cache, "_DERIVATIVE_MEMORY", ThumbnailMemoryCache(
        max_bytes=1024 * 1024, max_item_bytes=512 * 1024, ttl_seconds=90,
    ))
    with cache._LOCK:
        cache._STATUS.clear()
        cache._ACTIVE_WRITES.clear()
    yield
    with cache._LOCK:
        cache._STATUS.clear()
        cache._ACTIVE_WRITES.clear()


def params(*, tenant_id="boostgame", slot=1, quality="preview"):
    return dict(tenant_id=tenant_id, pack_id="sp-" + "b" * 40, slot=slot,
                quality=quality, sha256="c" * 64)


def test_canary_is_deny_all_until_enabled_for_this_tenant(monkeypatch):
    monkeypatch.delenv("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", raising=False)
    monkeypatch.delenv("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", raising=False)
    assert cache.config_for_tenant("boostgame") is None

    settings = SimpleNamespace(
        R2_VIDEO_CACHE_ENABLED=True,
        R2_BUCKET_NAME=CONFIG.bucket,
        R2_ACCESS_KEY=Secret(CONFIG.access_key),
        R2_ACCESS_KEY_ID=Secret(CONFIG.access_key),
        R2_SECRET_ACCESS_KEY=Secret(CONFIG.secret_key),
        R2_VIDEO_MEDIA_SIGNING_SECRET=Secret(CONFIG.signing_secret),
        r2_endpoint_url=CONFIG.endpoint,
        video_media_base_url=CONFIG.worker_origin,
    )
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    monkeypatch.setenv("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "true")
    assert cache.config_for_tenant("boostgame") is None
    monkeypatch.setenv("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "boostgame")
    assert cache.config_for_tenant("other-tenant") is None
    cfg = cache.config_for_tenant("boostgame")
    assert cfg is not None
    assert cfg.bucket == "creative-asset-video-cache"
    assert cfg.worker_enabled is False
    assert "fake-secret" not in repr(cfg)


def test_immutable_namespaced_key_and_worker_ticket():
    args = params()
    key = cache.object_key(CONFIG, **args)
    other = cache.object_key(CONFIG, **{**args, "tenant_id": "other"})
    assert key != other
    assert key.startswith("etsy-images/v1/")
    assert "/boostgame/" not in key
    assert key.endswith("/preview/" + "c" * 64 + "-w720v1.webp")
    assert not key.startswith("video-cache/")
    with pytest.raises(ValueError):
        cache.object_key(CONFIG, **{**args, "quality": "original"})
    with pytest.raises(ValueError):
        cache.object_key(CONFIG, **{**args, "pack_id": "../video-cache"})
    for slot in (10, 11, 12, 13):
        selected = cache.object_key(CONFIG, **{**args, "slot": slot})
        assert f"/{slot:02d}/preview/" in selected
    for slot in (0, 14, 15):
        with pytest.raises(ValueError):
            cache.object_key(CONFIG, **{**args, "slot": slot})
    assert cache.signed_worker_url(CONFIG, key) is None
    enabled = cache.ExistingR2Config(**{
        **CONFIG.__dict__, "worker_enabled": True,
    })
    signed = cache.signed_worker_url(enabled, key, now=1000)
    assert signed is not None
    url = urlsplit(signed)
    q = parse_qs(url.query)
    assert q["exp"] == ["1045"]
    msg = f"v1\netsy-image-read\n/{key}\n1045".encode("utf-8")
    mac = hmac.new(SECRET.encode("utf-8"), msg, sha256).digest()
    expected = base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
    assert q["sig"] == [expected]


def test_read_write_reuses_same_bucket_and_never_original(monkeypatch):
    s3 = MemoryS3()
    monkeypatch.setattr(cache, "_client", lambda _: s3)
    data = b"private-webp-small-preview"

    async def check():
        args = params()
        assert await cache.read_derivative(CONFIG, **args) is None
        assert s3.reads == 1
        # Negative cache prevents re-querying a missing object.
        assert await cache.read_derivative(CONFIG, **args) is None
        assert s3.reads == 1
        await cache.stage_derivative(CONFIG, **args, data=data)
        assert s3.puts == 1
        assert await cache.read_derivative(CONFIG, **args) == data
        assert s3.reads == 2
        # The second read is served from a bounded process-local memory cache.
        assert await cache.read_derivative(CONFIG, **args) == data
        assert s3.reads == 2
        assert await cache.read_derivative(CONFIG, **{
            **args, "tenant_id": "different"}) is None
        assert s3.reads == 3
        await cache.stage_derivative(CONFIG, **args, data=data)
        assert s3.puts == 1
        with pytest.raises(ValueError):
            await cache.read_derivative(CONFIG, **{**args, "quality": "original"})

    asyncio.run(check())


def test_corrupt_metadata_and_wrong_type_fail_closed(monkeypatch):
    s3 = MemoryS3()
    monkeypatch.setattr(cache, "_client", lambda _: s3)
    args = params(quality="thumbnail")
    key = cache.object_key(CONFIG, **args)
    s3.objects[key] = (b"fake-png", "image/png", {"cam-sha256": "0" * 64})
    async def check():
        assert await cache.read_derivative(CONFIG, **args) is None
        cache._mark(key, True)
        s3.objects[key] = (b"fake-png", "text/html",
                           {"cam-sha256": sha256(b"fake-png").hexdigest()})
        assert await cache.read_derivative(CONFIG, **args) is None
    asyncio.run(check())


def test_r2_errors_fall_back_and_upload_is_bounded(monkeypatch):
    class BrokenS3(MemoryS3):
        def get_object(self, **kwargs):
            raise ClientError({
                "Error": {"Code": "AccessDenied", "Message": "private credentials"},
                "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject")
        def put_object(self, **kwargs):
            raise ClientError({
                "Error": {"Code": "AccessDenied", "Message": "private credentials"},
                "ResponseMetadata": {"HTTPStatusCode": 403}}, "PutObject")
    monkeypatch.setattr(cache, "_client", lambda _: BrokenS3())
    async def check():
        assert await cache.read_derivative(CONFIG, **params()) is None
        assert await cache.stage_derivative(
            CONFIG, **params(), data=b"cache-fallback") is None
        assert await cache.read_derivative(CONFIG, **params()) is None
        assert await cache.stage_derivative(
            CONFIG, **params(quality="thumbnail"), data=b"x" * (2 * 1024 * 1024 + 1)) is None
    asyncio.run(check())
