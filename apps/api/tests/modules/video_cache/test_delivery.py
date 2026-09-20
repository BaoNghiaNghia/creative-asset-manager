import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.config import Settings
from app.modules.video_cache.delivery import (
    VideoCacheDeliveryService, VideoDeliveryNotConfigured, VideoDeliveryUnavailable,
    canonical_read_message, sign_read_path,
)
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.service import video_cache_key

VECTOR = json.loads((Path(__file__).resolve().parents[5] /
    "infrastructure/cloudflare/r2-video-worker/test/vectors.json").read_text(encoding="utf-8"))
SECRET = VECTOR["secret"]


def config(**updates):
    values = dict(R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="test-account",
        R2_BUCKET_NAME="test-bucket", R2_ACCESS_KEY_ID="fake-id",
        R2_SECRET_ACCESS_KEY="fake-key")
    values.update(updates)
    return Settings(_env_file=None, **values)


def row(**updates):
    values = dict(tenant_id="test-tenant", content_hash="0123456789abcdef" * 4,
        mime_type="video/mp4", status="ready", size_bytes=25)
    values.update(updates)
    if "r2_key" not in values:
        values["r2_key"] = video_cache_key(values["tenant_id"], values["content_hash"])
    return VideoCacheObjectModel(**values)


def delivery_config(**updates):
    return config(R2_VIDEO_MEDIA_BASE_URL="https://media.example.test/",
        R2_VIDEO_MEDIA_SIGNING_SECRET=SECRET, **updates)


def test_cache_filling_is_independent_of_delivery_configuration():
    settings = config()
    assert settings.R2_VIDEO_CACHE_ENABLED and not settings.video_delivery_configured
    with pytest.raises(VideoDeliveryNotConfigured):
        VideoCacheDeliveryService(settings).create_signed_url(row())
    for partial in ({"R2_VIDEO_MEDIA_BASE_URL": "https://media.example.test"},
                    {"R2_VIDEO_MEDIA_SIGNING_SECRET": SECRET}):
        assert not config(**partial).video_delivery_configured


def test_valid_config_ready_url_expiry_and_redacted_repr(caplog):
    settings = delivery_config()
    assert settings.video_delivery_configured
    assert settings.video_media_base_url == "https://media.example.test"
    service = VideoCacheDeliveryService(settings, clock=lambda: VECTOR["expires_at"] - 600)
    ticket = service.create_signed_url(row())
    assert ticket.expires_at == VECTOR["expires_at"]
    assert SECRET not in str(settings.model_dump(mode="json"))
    assert ticket.url == ("https://media.example.test" + VECTOR["pathname"] +
        f"?v=1&exp={VECTOR['expires_at']}&sig={VECTOR['signature']}")
    assert set(parse_qs(urlsplit(ticket.url).query)) == {"v", "exp", "sig"}
    assert SECRET not in repr(settings) + repr(ticket) + caplog.text
    assert ticket.url not in repr(ticket)


@pytest.mark.parametrize("base", (
    "https://user:pass@media.example.test", "https://media.example.test/path",
    "https://media.example.test?x=1", "https://media.example.test#fragment",
    "http://media.example.test", "ftp://media.example.test", "//media.example.test",
    "https://media.example.test/%2e%2e", "https://media.example.test:99999",
    "https://media.example.test\\attacker.test", "https://media.example.test\n",
    "https://a..b", "https://-bad.example.test",
))
def test_invalid_media_origin(base):
    with pytest.raises(ValueError):
        config(R2_VIDEO_MEDIA_BASE_URL=base)


def test_local_http_is_dev_only():
    assert config(R2_VIDEO_MEDIA_BASE_URL="http://127.0.0.1:8787").video_media_base_url == "http://127.0.0.1:8787"
    with pytest.raises(ValueError, match="R2_VIDEO_MEDIA_BASE_URL"):
        config(APP_ENV="production", R2_VIDEO_MEDIA_BASE_URL="http://localhost:8787")


@pytest.mark.parametrize("ttl", (0, -1, 3601))
def test_invalid_ttl(ttl):
    with pytest.raises(ValueError, match="R2_VIDEO_MEDIA_TICKET_TTL_SECONDS"):
        config(R2_VIDEO_MEDIA_TICKET_TTL_SECONDS=ttl)


@pytest.mark.parametrize("secret", ("short", "x" * 64, "long secret with spaces not valid 123456"))
def test_weak_secret_rejected_without_echo(secret):
    with pytest.raises(ValueError) as error:
        config(R2_VIDEO_MEDIA_SIGNING_SECRET=secret)
    assert secret not in str(error.value)


@pytest.mark.parametrize("status", ("preparing", "retry", "failed", "deleting"))
def test_only_ready_can_be_signed(status):
    with pytest.raises(VideoDeliveryUnavailable):
        VideoCacheDeliveryService(delivery_config()).create_signed_url(row(status=status))


@pytest.mark.parametrize("updates", (
    {"mime_type": "image/jpeg"}, {"size_bytes": 0},
    {"r2_key": "outside-video-cache/x"},
    {"r2_key": "video-cache/other-tenant/" + "a" * 64 + "/original"},
    {"tenant_id": "other/tenant", "r2_key": "video-cache/other/tenant/x"},
))
def test_non_video_or_invalid_key_cannot_be_signed(updates):
    with pytest.raises(VideoDeliveryUnavailable):
        VideoCacheDeliveryService(delivery_config()).create_signed_url(row(**updates))


def test_fixed_vector_and_path_expiry_binding():
    assert canonical_read_message(VECTOR["pathname"], VECTOR["expires_at"]).decode() == VECTOR["canonical"]
    assert sign_read_path(SECRET, VECTOR["pathname"], VECTOR["expires_at"]) == VECTOR["signature"]
    assert sign_read_path(SECRET, VECTOR["pathname"], VECTOR["expires_at"] + 1) != VECTOR["signature"]
    changed = VECTOR["pathname"].replace("test-tenant", "other-tenant")
    assert sign_read_path(SECRET, changed, VECTOR["expires_at"]) != VECTOR["signature"]
    for bad in (VECTOR["pathname"] + "%2f", VECTOR["pathname"] + "\\foo",
                VECTOR["pathname"].replace("test-tenant", "..")):
        with pytest.raises(VideoDeliveryUnavailable):
            sign_read_path(SECRET, bad, VECTOR["expires_at"])
