from __future__ import annotations

import logging
import httpx
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.providers.google.drive import GoogleDriveClient, close_media_stream, open_media_stream

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class MediaDimensions:
    width: int | None
    height: int | None
    source: str

    @property
    def available(self) -> bool:
        return self.width is not None and self.height is not None

def _positive_pair(width: Any, height: Any) -> tuple[int, int] | None:
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None

def _metadata_pair(metadata: dict[str, Any]) -> tuple[int, int] | None:
    for candidate in (metadata.get("imageMediaMetadata"), metadata.get("image_media_metadata"), metadata):
        if isinstance(candidate, dict):
            pair = _positive_pair(candidate.get("width") or candidate.get("image_width"), candidate.get("height") or candidate.get("image_height"))
            if pair:
                return pair
    return None

def _is_image(source: SourceAssetModel) -> bool:
    mime = (source.mime_type or "").lower()
    name = (source.filename or "").lower()
    return mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif", ".heif", ".heic", ".tif", ".tiff"))

class MediaDimensionsResolver:
    def __init__(self, session, *, max_source_bytes: int | None = None):
        self.session = session
        self.max_source_bytes = max_source_bytes or get_settings().AI_ANALYSIS_MAX_SOURCE_BYTES

    async def resolve(self, *, source: SourceAssetModel, external: ExternalSourceModel, token: str | None) -> MediaDimensions:
        started = time.monotonic()
        metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
        pair = _metadata_pair(metadata)
        if pair:
            return MediaDimensions(*pair, "database")
        provider_pair = None
        if external.source_type == "google_drive" and token:
            try:
                async with GoogleDriveClient(token) as client:
                    raw = await client.get_image_dimensions(source.external_asset_id)
                    provider_pair = _positive_pair(*raw) if raw else None
            except Exception as exc:
                logger.info("media_dimensions_provider_failed item_id=%s external_source_id=%s error=%s", source.external_asset_id, source.external_source_id, type(exc).__name__)
        if provider_pair:
            self._persist(source, provider_pair, "drive_metadata")
            return MediaDimensions(*provider_pair, "drive_metadata")
        if _is_image(source) and token and external.source_type == "google_drive":
            try:
                result = await self._header_fallback(source.external_asset_id, token)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                failure_source = "provider_forbidden" if status in {401, 403} else "provider_missing" if status == 404 else "unavailable"
                logger.warning("media_dimensions_optional_failure item_id=%s provider_item_id=%s external_source_id=%s provider=google-drive status=%s stage=media_header", source.id, source.external_asset_id, source.external_source_id, status)
                return MediaDimensions(None, None, failure_source)
            except (httpx.HTTPError, TimeoutError, OSError, ValueError, UnidentifiedImageError) as exc:
                logger.warning("media_dimensions_optional_failure item_id=%s provider_item_id=%s external_source_id=%s provider=google-drive status=unknown stage=media_header error=%s", source.id, source.external_asset_id, source.external_source_id, type(exc).__name__)
                return MediaDimensions(None, None, "media_header_failed")
            if result:
                self._persist(source, result, "media_header")
                return MediaDimensions(*result, "media_header")
        logger.info("media_dimensions_unavailable item_id=%s external_source_id=%s duration_ms=%s", source.external_asset_id, source.external_source_id, round((time.monotonic()-started)*1000))
        return MediaDimensions(None, None, "unavailable")

    def _persist(self, source: SourceAssetModel, pair: tuple[int, int], resolution_source: str) -> None:
        metadata = dict(source.source_metadata or {})
        metadata.update({"image_width": pair[0], "image_height": pair[1], "resolution_source": resolution_source, "resolution_provider_version": source.provider_version})
        source.source_metadata = metadata
        self.session.flush()

    async def _header_fallback(self, item_id: str, token: str) -> tuple[int, int] | None:
        path = None
        client = response = None
        total = 0
        try:
            client, response = await open_media_stream(token, item_id, None)
            with tempfile.NamedTemporaryFile(prefix="cam-dimensions-", delete=False) as handle:
                path = handle.name
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > self.max_source_bytes:
                        return None
                    handle.write(chunk)
            try:
                import pyvips
                image = pyvips.Image.new_from_file(path, access="sequential")
                pair = _positive_pair(image.width, image.height)
                if pair:
                    return pair
            except (ImportError, OSError, ValueError):
                pass
            try:
                with Image.open(path) as image:
                    return _positive_pair(image.width, image.height)
            except (UnidentifiedImageError, OSError, ValueError):
                return None
        finally:
            if client is not None and response is not None:
                await close_media_stream(client, response)
            if path:
                try: os.unlink(path)
                except FileNotFoundError: pass
