import asyncio
import hashlib

import pytest

from app.core.config import Settings
from app.modules.video_cache.service import (
    VideoCacheIntegrityError, VideoCacheService, video_cache_key,
)
from app.providers.cloudflare.r2 import R2Adapter, R2NotFound, R2ProviderError


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def chunks(*values):
    for value in values:
        yield value


class FakeR2:
    def __init__(self):
        self.parts = []
        self.aborted = False
        self.deleted = False
        self.completed = False
        self.blob = b""

    async def create_multipart_upload(self, key, mime_type):
        self.key = key
        self.mime_type = mime_type
        return "upload"

    async def upload_part(self, key, upload_id, part_number, body):
        self.parts.append(body)
        return str(part_number)

    async def complete_multipart_upload(self, key, upload_id, parts):
        self.blob = b"".join(self.parts)
        self.completed = True
        return "etag"

    async def head_object(self, key):
        from app.providers.cloudflare.r2 import R2ObjectHead
        return R2ObjectHead(len(self.blob), "etag")

    async def abort_multipart_upload(self, key, upload_id):
        self.aborted = True

    async def delete_object(self, key):
        self.deleted = True


def test_disabled_settings_allow_empty_credentials_and_derive_endpoint():
    settings = Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=False, R2_ACCOUNT_ID="account")
    assert settings.r2_endpoint_url == "https://account.r2.cloudflarestorage.com"


@pytest.mark.parametrize("values", [
    {"R2_ACCOUNT_ID": ""}, {"R2_BUCKET_NAME": ""}, {"R2_ACCESS_KEY_ID": ""},
    {"R2_SECRET_ACCESS_KEY": ""},
])
def test_enabled_requires_every_credential(values):
    kwargs = dict(R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="account",
                  R2_BUCKET_NAME="bucket", R2_ACCESS_KEY_ID="key",
                  R2_SECRET_ACCESS_KEY="do-not-print-me")
    kwargs.update(values)
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, **kwargs)
    assert "do-not-print-me" not in str(error.value)


@pytest.mark.parametrize("overrides", [
    {"R2_VIDEO_CACHE_SOFT_LIMIT_BYTES": 0},
    {"R2_VIDEO_CACHE_HARD_LIMIT_BYTES": 8_000_000_000},
    {"R2_VIDEO_CACHE_HARD_LIMIT_BYTES": 10_000_000_000},
    {"R2_VIDEO_CACHE_MAX_OBJECT_BYTES": 10_000_000_000},
    {"R2_VIDEO_CACHE_ACCESS_TOUCH_SECONDS": 0},
])
def test_invalid_limits_rejected(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("endpoint", [
    "https://attacker.example", "http://account.r2.cloudflarestorage.com",
    "https://account.r2.cloudflarestorage.com@attacker.example",
    "https://account.r2.cloudflarestorage.com/path",
])
def test_endpoint_cannot_redirect_credentials(endpoint):
    with pytest.raises(ValueError):
        Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="account",
                 R2_BUCKET_NAME="bucket", R2_ACCESS_KEY_ID="key",
                 R2_SECRET_ACCESS_KEY="do-not-print-me", R2_ENDPOINT=endpoint)

def test_key_is_deterministic_and_cannot_escape_prefix():
    hash_value = digest(b"video")
    assert video_cache_key("tenant-1", hash_value) == f"video-cache/tenant-1/{hash_value}/original"
    for tenant in ("../other", "tenant/a", "", "a%2fb"):
        with pytest.raises(ValueError):
            video_cache_key(tenant, hash_value)
    with pytest.raises(ValueError):
        video_cache_key("tenant", "../hash")


def test_upload_reports_safe_operation_phases():
    data = b"video bytes"
    phases: list[str] = []
    fake = FakeR2()
    result = asyncio.run(
        VideoCacheService(fake, max_object_bytes=100).upload_original(
            chunks(data),
            tenant_id="tenant",
            content_hash_expected=digest(data),
            expected_size_bytes=len(data),
            mime_type="video/mp4",
            on_phase=phases.append,
        )
    )
    assert result.size_bytes == len(data)
    assert phases == [
        "validate",
        "r2_upload_init",
        "r2_upload_part",
        "r2_upload_complete",
        "r2_verify",
    ]


def test_upload_is_bounded_byte_exact_and_video_only():
    data = bytes(range(256)) * (11 * 1024 * 1024 // 256)
    fake = FakeR2()
    service = VideoCacheService(fake, max_object_bytes=20 * 1024 * 1024, part_size=5 * 1024 * 1024)
    result = asyncio.run(service.upload_original(
        chunks(data[:17], data[17:2000], data[2000:]), tenant_id="tenant",
        content_hash_expected=digest(data), expected_size_bytes=len(data), mime_type="video/mp4",
    ))
    assert fake.blob == data
    assert max(map(len, fake.parts)) <= 5 * 1024 * 1024
    assert result.size_bytes == len(data) and result.content_hash == digest(data)
    assert fake.mime_type == "video/mp4"
    for mime in ("image/jpeg", "audio/mp3", "application/pdf", "video/mp4; charset=x"):
        with pytest.raises(ValueError):
            asyncio.run(service.upload_original(
                chunks(data), tenant_id="tenant", content_hash_expected=digest(data),
                expected_size_bytes=len(data), mime_type=mime,
            ))


@pytest.mark.parametrize("wrong_hash,wrong_size", [(True, False), (False, True)])
def test_integrity_failure_aborts_before_completion(wrong_hash, wrong_size):
    data = b"video bytes"
    fake = FakeR2()
    with pytest.raises(VideoCacheIntegrityError):
        asyncio.run(VideoCacheService(fake, max_object_bytes=100).upload_original(
            chunks(data), tenant_id="tenant",
            content_hash_expected=digest(b"wrong") if wrong_hash else digest(data),
            expected_size_bytes=len(data) + 1 if wrong_size else len(data),
            mime_type="video/mp4",
        ))
    assert fake.aborted and not fake.completed


def test_stream_failure_and_cancellation_abort():
    async def fails():
        yield b"first"
        raise OSError("source failed")

    async def cancelled():
        yield b"first"
        raise asyncio.CancelledError()

    for stream, error in ((fails(), OSError), (cancelled(), asyncio.CancelledError)):
        fake = FakeR2()
        with pytest.raises(error):
            asyncio.run(VideoCacheService(fake, max_object_bytes=100).upload_original(
                stream, tenant_id="tenant", content_hash_expected=digest(b"first"),
                expected_size_bytes=5, mime_type="video/mp4",
            ))
        assert fake.aborted


def test_key_must_be_server_generated_and_oversize_rejected():
    fake = FakeR2()
    service = VideoCacheService(fake, max_object_bytes=3)
    with pytest.raises(ValueError):
        asyncio.run(service.upload_original(
            chunks(b"x"), tenant_id="tenant", content_hash_expected=digest(b"x"),
            expected_size_bytes=1, mime_type="video/mp4", target_key="other",
        ))
    with pytest.raises(VideoCacheIntegrityError):
        asyncio.run(service.upload_original(
            chunks(b"abcd"), tenant_id="tenant", content_hash_expected=digest(b"abcd"),
            expected_size_bytes=None, mime_type="video/mp4",
        ))
    assert fake.aborted


class Client:
    def __init__(self, code="404", status=404):
        self.code, self.status = code, status
    def head_object(self, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError(
            {"Error": {"Code": self.code, "Message": "SECRET signed-url"},
             "ResponseMetadata": {"HTTPStatusCode": self.status}}, "HeadObject"
        )
    def delete_object(self, **kwargs):
        return self.head_object()


def test_provider_not_found_delete_and_safe_transient_classification():
    pytest.importorskip("botocore.exceptions")
    settings = Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="account",
                        R2_BUCKET_NAME="bucket", R2_ACCESS_KEY_ID="key",
                        R2_SECRET_ACCESS_KEY="secret")
    adapter = R2Adapter(settings, client=Client())
    assert asyncio.run(adapter.object_exists("key")) is False
    asyncio.run(adapter.delete_object("key"))
    with pytest.raises(R2NotFound):
        asyncio.run(adapter.head_object("key"))
    adapter = R2Adapter(settings, client=Client("SlowDown", 503))
    with pytest.raises(R2ProviderError) as error:
        asyncio.run(adapter.head_object("key"))
    assert error.value.retryable
    assert "SECRET" not in str(error.value)
    adapter = R2Adapter(settings, client=Client("AccessDenied", 403))
    with pytest.raises(R2ProviderError) as error:
        asyncio.run(adapter.head_object("key"))
    assert not error.value.retryable

def test_completed_object_with_wrong_remote_size_is_deleted():
    from app.providers.cloudflare.r2 import R2ObjectHead

    class WrongHead(FakeR2):
        async def head_object(self, key):
            return R2ObjectHead(len(self.blob) + 1, "etag")

    fake = WrongHead()
    data = b"original"
    with pytest.raises(VideoCacheIntegrityError):
        asyncio.run(VideoCacheService(fake, max_object_bytes=100).upload_original(
            chunks(data), tenant_id="tenant", content_hash_expected=digest(data),
            expected_size_bytes=len(data), mime_type="video/mp4",
        ))
    assert fake.completed and fake.deleted


def test_provider_calls_run_off_event_loop():
    import threading

    class ThreadClient:
        thread_name = None
        def head_object(self, **kwargs):
            self.thread_name = threading.current_thread().name
            return {"ContentLength": 10, "ETag": "etag"}

    settings = Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="account",
                        R2_BUCKET_NAME="bucket", R2_ACCESS_KEY_ID="key",
                        R2_SECRET_ACCESS_KEY="secret")
    client = ThreadClient()
    head = asyncio.run(R2Adapter(settings, client=client).head_object("key"))
    assert head.size_bytes == 10
    assert client.thread_name != threading.current_thread().name
