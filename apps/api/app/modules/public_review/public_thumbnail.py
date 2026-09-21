from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.modules.explorer.cache import CachedThumbnail, thumbnail_cache, thumbnail_negative_cache
from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.providers.google.drive import (
    GoogleDriveThumbnailUnavailable,
    close_thumbnail_stream as close_google_thumbnail,
    open_thumbnail_stream as open_google_thumbnail,
)
from app.providers.microsoft.onedrive import (
    OneDriveThumbnailUnavailable,
    close_thumbnail_stream as close_onedrive_thumbnail,
    open_thumbnail_stream as open_onedrive_thumbnail,
)

MAX_THUMBNAIL_BYTES = 16 * 1024 * 1024
_VIDEO_THUMBNAIL_PLACEHOLDER = b"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180" role="img" aria-label="Video thumbnail unavailable"><rect width="320" height="180" fill="#edf2fb"/><rect x="1" y="1" width="318" height="178" rx="10" fill="none" stroke="#cbd8ed" stroke-width="2"/><circle cx="160" cy="82" r="28" fill="#4163d8"/><path d="M151 66v32l25-16z" fill="#fff"/><text x="160" y="143" text-anchor="middle" fill="#50627f" font-family="Arial, sans-serif" font-size="14">Video preview unavailable</text></svg>"""


class PublicThumbnailUnavailable(ValueError):
    """A share-authorized source asset has no safe thumbnail."""


@dataclass(frozen=True, slots=True)
class PublicThumbnail:
    content: bytes
    content_type: str


class PublicThumbnailResolver:
    """Fetch a bounded provider thumbnail after the public route authorizes scope."""

    def __init__(
        self,
        session_factory: type[Session] | object,
        *,
        google_http_client: httpx.AsyncClient | None = None,
        onedrive_http_client: httpx.AsyncClient | None = None,
    ):
        self.session_factory = session_factory
        self.google_http_client = google_http_client
        self.onedrive_http_client = onedrive_http_client

    async def load(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        external_asset_id: str,
        filename: str | None,
        mime_type: str | None,
        thumbnail_url_hint: str | None = None,
        variant: str = "grid",
    ) -> PublicThumbnail:
        with self.session_factory() as session:
            try:
                resolved = await TenantSourceResolver(session).resolve(
                    tenant_id=tenant_id,
                    external_source_id=external_source_id,
                )
            except Exception as exc:
                raise PublicThumbnailUnavailable("source connection is unavailable") from exc

        source_type = resolved.source_type
        source_id = resolved.external_source_id
        effective_mime_type = infer_media_type(filename, mime_type)

        if source_type not in {"google_drive", "onedrive"}:
            raise PublicThumbnailUnavailable("source provider is unsupported")

        if variant not in {"grid", "preview"}:
            raise PublicThumbnailUnavailable("thumbnail variant is unsupported")
        cache_version = (
            "public-preview-v1"
            if variant == "preview"
            else ("public-medium-v1" if source_type == "onedrive" else "public-grid-v2")
        )
        cache_key = (str(tenant_id), str(source_id), str(external_asset_id), cache_version)

        if thumbnail_negative_cache.get(cache_key):
            return self._video_fallback(effective_mime_type)

        async def load_thumbnail() -> CachedThumbnail:
            client = upstream = None
            try:
                if source_type == "google_drive":
                    client, upstream = await open_google_thumbnail(
                        resolved.access_token,
                        external_asset_id,
                        cache_key=(str(tenant_id), str(source_id), str(external_asset_id)),
                        http_client=self.google_http_client,
                        thumbnail_url_hint=thumbnail_url_hint,
                        size_pixels=2048 if variant == "preview" else 640,
                    )
                else:
                    client, upstream = await open_onedrive_thumbnail(
                        resolved.access_token,
                        external_asset_id,
                        http_client=self.onedrive_http_client,
                        size="large" if variant == "preview" else "medium",
                    )
                content = bytearray()
                async for chunk in upstream.aiter_raw():
                    content.extend(chunk)
                    if len(content) > MAX_THUMBNAIL_BYTES:
                        raise PublicThumbnailUnavailable("thumbnail is too large")
                return CachedThumbnail(
                    content=bytes(content),
                    content_type=upstream.headers.get("content-type") or "image/jpeg",
                    headers=(),
                )
            finally:
                if client is not None and upstream is not None:
                    if source_type == "google_drive":
                        await close_google_thumbnail(
                            client,
                            upstream,
                            client is not self.google_http_client,
                        )
                    else:
                        await close_onedrive_thumbnail(
                            client,
                            upstream,
                            client is not self.onedrive_http_client,
                        )

        try:
            value = await thumbnail_cache.get_or_load(cache_key, load_thumbnail)
            return PublicThumbnail(content=value.content, content_type=value.content_type)
        except (GoogleDriveThumbnailUnavailable, OneDriveThumbnailUnavailable):
            thumbnail_negative_cache.put(cache_key, True)
            return self._video_fallback(effective_mime_type)
        except (httpx.HTTPError, PermissionError, PublicThumbnailUnavailable, ValueError) as exc:
            raise PublicThumbnailUnavailable("thumbnail is unavailable") from exc

    @staticmethod
    def _video_fallback(mime_type: str) -> PublicThumbnail:
        if mime_type.startswith("video/"):
            return PublicThumbnail(
                content=_VIDEO_THUMBNAIL_PLACEHOLDER,
                content_type="image/svg+xml",
            )
        raise PublicThumbnailUnavailable("thumbnail is unavailable")
