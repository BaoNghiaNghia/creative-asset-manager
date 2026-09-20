"""Authorized Public Review handoff to the private R2 video delivery path.

This module never grants Public Review access. Its caller must first authorize
the exact asset/source pair with SharePrincipal scope. Any rollout/config/cache
problem falls back to the existing provider stream.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    ):
        self.session_factory = session_factory
        self.settings = settings

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

        session_expiry = getattr(principal, "session_expires_at", None)
        if not isinstance(session_expiry, datetime):
            return None
        expiry_cap = _epoch(session_expiry)
        share_expiry = getattr(principal, "expires_at", None)
        if isinstance(share_expiry, datetime):
            expiry_cap = min(expiry_cap, _epoch(share_expiry))

        with self.session_factory() as session:
            try:
                runtime = VideoDeliveryRuntimeService(session, self.settings).get_status()
                if not runtime["effective_enabled"]:
                    return None
                repository = VideoCacheRepository(session)
                cache_object = repository.get_by_tenant_and_hash(tenant_id, content_hash)
                if (
                    cache_object is None
                    or cache_object.asset_id != asset_id
                    or cache_object.source_asset_id != source_asset_id
                ):
                    return None
                ticket = VideoCacheDeliveryService(self.settings).create_signed_url(
                    cache_object,
                    expires_at_cap=expiry_cap,
                )
                repository.touch_access(
                    tenant_id,
                    cache_object.id,
                    debounce_seconds=self.settings.R2_VIDEO_CACHE_ACCESS_TOUCH_SECONDS,
                )
                session.commit()
                return ticket
            except (
                SQLAlchemyError,
                VideoDeliveryError,
                VideoDeliveryRuntimeUnavailable,
                ValueError,
            ):
                session.rollback()
                return None
