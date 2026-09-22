"""Short-lived, server-only tickets for READY original-video cache objects.

This module issues capabilities; it does not authorize the caller. The future
Phase 4 integration must check tenant/share/asset/source scope before calling it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.service import video_cache_key, video_playback_key

_VIDEO_PATH = re.compile(
    r"/video-cache/([A-Za-z0-9][A-Za-z0-9_-]{0,254})/([0-9a-f]{64})/(original|playback\.mp4)",
    re.ASCII,
)


class VideoDeliveryError(ValueError):
    """A safe delivery failure; never includes a key, ticket, or secret."""


class VideoDeliveryNotConfigured(VideoDeliveryError):
    pass


class VideoDeliveryUnavailable(VideoDeliveryError):
    pass


@dataclass(frozen=True)
class SignedVideoDelivery:
    url: str = field(repr=False)
    expires_at: int
    size_bytes: int


def canonical_read_message(pathname: str, expires_at: int) -> bytes:
    match = _VIDEO_PATH.fullmatch(pathname)
    if match is None:
        raise VideoDeliveryUnavailable("Video delivery is unavailable")
    tenant_id, content_hash, variant = match.groups()
    expected_key = (
        video_cache_key(tenant_id, content_hash)
        if variant == "original"
        else video_playback_key(tenant_id, content_hash)
    )
    if pathname != "/" + expected_key:
        raise VideoDeliveryUnavailable("Video delivery is unavailable")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at <= 0:
        raise VideoDeliveryUnavailable("Video delivery is unavailable")
    return f"v1\nread\n{pathname}\n{expires_at}".encode("ascii")


def sign_read_path(secret: str, pathname: str, expires_at: int) -> str:
    message = canonical_read_message(pathname, expires_at)
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


class VideoCacheDeliveryService:
    def __init__(self, settings: Settings, *, clock: Callable[[], int] | None = None):
        self.settings = settings
        self._clock = clock or (lambda: int(time.time()))

    def create_signed_url(
        self,
        cache_object: VideoCacheObjectModel,
        *,
        expires_at_cap: int | None = None,
    ) -> SignedVideoDelivery:
        if not self.settings.video_delivery_configured:
            raise VideoDeliveryNotConfigured("Video delivery is not configured")
        try:
            original_key = video_cache_key(cache_object.tenant_id, cache_object.content_hash)
            playback_key = video_playback_key(cache_object.tenant_id, cache_object.content_hash)
        except (ValueError, AttributeError, TypeError):
            raise VideoDeliveryUnavailable("Video delivery is unavailable") from None
        if (
            cache_object.status != "ready"
            or not isinstance(cache_object.mime_type, str)
            or not cache_object.mime_type.startswith("video/")
            or cache_object.r2_key != original_key
            or not isinstance(cache_object.size_bytes, int)
            or cache_object.size_bytes <= 0
        ):
            raise VideoDeliveryUnavailable("Video delivery is unavailable")
        derived_ready = bool(
            self.settings.R2_VIDEO_PLAYBACK_DERIVED_ENABLED
            and cache_object.playback_status == "ready"
            and cache_object.playback_r2_key == playback_key
            and cache_object.playback_kind in {"faststart", "proxy_1080p"}
            and isinstance(cache_object.playback_size_bytes, int)
            and cache_object.playback_size_bytes > 0
        )
        expected_key = playback_key if derived_ready else original_key
        expected_size = cache_object.playback_size_bytes if derived_ready else cache_object.size_bytes
        current = self._clock()
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise VideoDeliveryUnavailable("Video delivery is unavailable")
        expires_at = current + self.settings.R2_VIDEO_MEDIA_TICKET_TTL_SECONDS
        if expires_at_cap is not None:
            if (
                isinstance(expires_at_cap, bool)
                or not isinstance(expires_at_cap, int)
                or expires_at_cap <= current
            ):
                raise VideoDeliveryUnavailable("Video delivery is unavailable")
            expires_at = min(expires_at, expires_at_cap)
        pathname = "/" + expected_key
        signature = sign_read_path(
            self.settings.R2_VIDEO_MEDIA_SIGNING_SECRET.get_secret_value(),
            pathname,
            expires_at,
        )
        url = (
            f"{self.settings.video_media_base_url}{pathname}"
            f"?v=1&exp={expires_at}&sig={signature}"
        )
        return SignedVideoDelivery(url=url, expires_at=expires_at, size_bytes=expected_size)
