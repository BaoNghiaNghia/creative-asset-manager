"""Authorized Public Review handoff to the private R2 video delivery path.

This module never grants Public Review access. Its caller must first authorize
the exact asset/source pair with SharePrincipal scope. Any rollout/config/cache
or health-guard problem falls back to the existing provider stream.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock, Thread
from time import monotonic, perf_counter
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
from app.modules.video_cache.fill import VideoCacheFillService
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
from app.providers.cloudflare.r2 import R2Adapter


_access_touch_lock = Lock()
_access_touch_deadline: dict[tuple[str, str], float] = {}

def _should_touch_access(tenant_id: str, record_id: str, debounce_seconds: int) -> bool:
    now = monotonic()
    key = (tenant_id, record_id)
    with _access_touch_lock:
        deadline = _access_touch_deadline.get(key, 0.0)
        if deadline > now:
            return False
        if len(_access_touch_deadline) >= 4096:
            expired = [item for item, value in _access_touch_deadline.items() if value <= now]
            for item in expired[:2048]:
                _access_touch_deadline.pop(item, None)
        _access_touch_deadline[key] = now + max(1, debounce_seconds)
        return True


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
        probe_scheduler: Callable[[Callable[[], None]], None] | None = None,
        fill_service_factory: Callable[[], VideoCacheFillService] | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.guard = guard or VIDEO_DELIVERY_GUARD
        self.probe = probe or probe_signed_video_head
        self.probe_scheduler = probe_scheduler or self._start_probe_thread
        self.fill_service_factory = fill_service_factory or (
            lambda: VideoCacheFillService(
                self.session_factory,
                self.settings,
                R2Adapter(self.settings),
            )
        )

    @staticmethod
    def _finish(started: float, counter: str) -> None:
        emit_counter(counter)
        observe_delivery_decision_ms((perf_counter() - started) * 1000.0)

    @staticmethod
    def _start_probe_thread(run: Callable[[], None]) -> None:
        Thread(
            target=run,
            name="video-delivery-guard-probe",
            daemon=True,
        ).start()

    def _schedule_probe(self, ticket: SignedVideoDelivery, *, expected_size: int) -> None:
        def run() -> None:
            try:
                success = bool(self.probe(
                    ticket,
                    expected_size=expected_size,
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

        try:
            self.probe_scheduler(run)
        except Exception:
            opened = self.guard.complete_probe(self.settings, success=False)
            emit_counter("video_cdn_probe_failure_total")
            if opened:
                emit_counter("video_cdn_guard_open_total")

    async def resolve(
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
                    # Admission owns its own transaction and may await quota cleanup.
                    # Release this read-only resolver session first so we do not hold a
                    # pooled DB connection across that async boundary (and so SQLite
                    # StaticPool tests do not nest independent sessions on one handle).
                    session.rollback()
                    session.close()
                    # Authorization and rollout scope have already succeeded. Admission
                    # revalidates the exact tenant/asset/source pair in its own
                    # transaction and only enqueues the durable worker job; playback
                    # remains on the provider path for this first request.
                    try:
                        await self.fill_service_factory().ensure_video_cache_fill(
                            tenant_id=tenant_id,
                            asset_id=asset_id,
                            source_asset_id=source_asset_id,
                            content_hash=content_hash,
                        )
                    except Exception:
                        # R2 is an optional acceleration layer. Admission failures must
                        # not make an authorized source video unavailable, and provider
                        # exception details must not enter the public response or logs.
                        session.rollback()
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
                if decision.schedule_probe:
                    self._schedule_probe(
                        ticket,
                        expected_size=ticket.size_bytes,
                    )
                if decision.action == "fallback":
                    self._finish(started, "video_cdn_fallback_guard_total")
                    return None

                if _should_touch_access(
                    tenant_id,
                    cache_object.id,
                    self.settings.R2_VIDEO_CACHE_ACCESS_TOUCH_SECONDS,
                ):
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
