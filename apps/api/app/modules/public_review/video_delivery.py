"""Authorized Public Review handoff to the private R2 video delivery path.

This module never grants Public Review access. Its caller must first authorize
the exact asset/source pair with SharePrincipal scope. Any rollout/config/cache
or health-guard problem falls back to the existing provider stream.
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.model import AssetModel, SourceAssetModel
from app.modules.explorer.media_types import infer_media_type
from app.modules.public_review.authorization import SharePrincipal
from app.modules.video_cache.delivery import (
    SignedVideoDelivery,
    VideoCacheDeliveryService,
    VideoDeliveryError,
)
from app.modules.video_cache.guard import (
    VIDEO_DELIVERY_GUARD,
    VideoDeliveryCircuitBreaker,
    probe_signed_video_head,
)
from app.modules.video_cache.metrics import emit_counter, observe_delivery_decision_ms
from app.modules.video_cache.repository import VideoCacheRepository
from app.modules.video_cache.runtime import (
    VideoDeliveryRuntimeService,
    VideoDeliveryRuntimeUnavailable,
)


def _epoch(value: datetime) -> int:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(normalized.timestamp())


class PublicVideoDeliveryResolver:
    """Resolve an already-authorized Public Review video to a short CDN ticket."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        guard: VideoDeliveryCircuitBreaker | None = None,
        probe: Callable[..., bool] | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.guard = guard or VIDEO_DELIVERY_GUARD
        self.probe = probe or probe_signed_video_head

    @staticmethod
    def _finish(started: float, counter: str) -> None:
        emit_counter(counter)
        observe_delivery_decision_ms((perf_counter() - started) * 1000.0)

    def resolve(
        self,
        *,
        principal: SharePrincipal,
        asset: AssetModel,
        source: SourceAssetModel,
    ) -> SignedVideoDelivery | None:
        tenant_id = getattr(principal, "tenant_id", None)
        asset_tenant = getattr(asset, "tenant_id", None)
        source_tenant = getattr(source, "tenant_id", None)
        content_hash = getattr(asset, "content_hash", None)
        asset_id = getattr(asset, "id", None)
        source_asset_id = getattr(source, "id", None)
        if (
            not isinstance(tenant_id, str)
            or tenant_id != asset_tenant
            or tenant_id != source_tenant
            or not isinstance(asset_id, str)
            or not isinstance(source_asset_id, str)
            or not isinstance(content_hash, str)
            or not infer_media_type(
                getattr(source, "filename", None),
                getattr(source, "mime_type", None),
                getattr(asset, "mime_type", None),
            ).startswith("video/")
        ):
            return None

        started = perf_counter()
        session_expiry = getattr(principal, "session_expires_at", None)
        if not isinstance(session_expiry, datetime):
            self._finish(started, "video_cdn_fallback_delivery_error_total")
            return None
        expiry_cap = _epoch(session_expiry)
        share_expiry = getattr(principal, "expires_at", None)
        if isinstance(share_expiry, datetime):
            expiry_cap = min(expiry_cap, _epoch(share_expiry))

        with self.session_factory() as session:
            try:
                runtime = VideoDeliveryRuntimeService(session, self.settings).get_status()
                if not runtime["effective_enabled"]:
                    self._finish(started, "video_cdn_fallback_runtime_total")
                    return None
                if not self.settings.video_delivery_tenant_allowed(tenant_id):
                    self._finish(started, "video_cdn_fallback_rollout_scope_total")
                    return None

                repository = VideoCacheRepository(session)
                cache_object = repository.get_by_tenant_and_hash(tenant_id, content_hash)
                if cache_object is None:
                    self._finish(started, "video_cdn_fallback_cache_miss_total")
                    return None
                if (
                    cache_object.asset_id != asset_id
                    or cache_object.source_asset_id != source_asset_id
                ):
                    self._finish(started, "video_cdn_fallback_cache_identity_total")
                    return None

                ticket = VideoCacheDeliveryService(self.settings).create_signed_url(
                    cache_object,
                    expires_at_cap=expiry_cap,
                )

                decision = self.guard.before_candidate(self.settings)
                if decision.action == "fallback":
                    self._finish(started, "video_cdn_fallback_guard_total")
                    return None
                if decision.action == "probe":
                    try:
                        success = bool(self.probe(
                            ticket,
                            expected_size=cache_object.size_bytes,
                            timeout_seconds=self.settings.VIDEO_CDN_DELIVERY_GUARD_TIMEOUT_SECONDS,
                        ))
                    except Exception:
                        success = False
                    opened = self.guard.complete_probe(self.settings, success=success)
                    emit_counter(
                        "video_cdn_probe_success_total"
                        if success
                        else "video_cdn_probe_failure_total"
                    )
                    if opened:
                        emit_counter("video_cdn_guard_open_total")
                    if not success:
                        self._finish(started, "video_cdn_fallback_guard_total")
                        return None

                repository.touch_access(
                    tenant_id,
                    cache_object.id,
                    debounce_seconds=self.settings.R2_VIDEO_CACHE_ACCESS_TOUCH_SECONDS,
                )
                session.commit()
                self._finish(started, "video_cdn_redirect_total")
                return ticket
            except VideoDeliveryError:
                session.rollback()
                self._finish(started, "video_cdn_fallback_delivery_error_total")
                return None
            except (
                SQLAlchemyError,
                VideoDeliveryRuntimeUnavailable,
                ValueError,
            ):
                session.rollback()
                self._finish(started, "video_cdn_fallback_internal_total")
                return None
