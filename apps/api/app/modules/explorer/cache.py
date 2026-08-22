from __future__ import annotations

from dataclasses import dataclass

from app.common.cache import (
    AsyncSingleFlightTTLCache,
    BoundedTTLCache,
    ByteSizeTTLCache,
)


@dataclass(frozen=True, slots=True)
class DriveSourceMetadata:
    external_source_id: str
    oauth_connection_id: str
    provider_account_id: str


DriveSourceKey = tuple[str, str]

drive_source_cache: BoundedTTLCache[DriveSourceKey, DriveSourceMetadata] = (
    BoundedTTLCache(max_entries=512, ttl_seconds=45)
)


def invalidate_drive_source(
    *, tenant_id: str, external_source_id: str | None = None
) -> int:
    return drive_source_cache.invalidate_where(
        lambda key: key[0] == tenant_id
        and (external_source_id is None or key[1] in {"", external_source_id})
    )


DriveListingKey = tuple[str, str, str, str, int, str]

drive_listing_cache = AsyncSingleFlightTTLCache(
    BoundedTTLCache[DriveListingKey, object](max_entries=1024, ttl_seconds=30)
)


def invalidate_drive_listings(
    *,
    tenant_id: str,
    external_source_id: str,
    parent_id: str | None = None,
) -> int:
    return drive_listing_cache.invalidate_where(
        lambda key: key[0] == tenant_id
        and key[1] == external_source_id
        and (parent_id is None or key[2] == parent_id)
    )


@dataclass(frozen=True, slots=True)
class CachedThumbnail:
    content: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...]


ThumbnailKey = tuple[str, str, str, str]

thumbnail_cache = AsyncSingleFlightTTLCache(
    ByteSizeTTLCache[ThumbnailKey, CachedThumbnail](
        max_entries=4096,
        max_bytes=256 * 1024 * 1024,
        ttl_seconds=3600,
        size_of=lambda value: len(value.content),
    )
)
thumbnail_negative_cache: BoundedTTLCache[ThumbnailKey, bool] = BoundedTTLCache(
    max_entries=4096, ttl_seconds=60
)


def invalidate_thumbnail(
    *, tenant_id: str, external_source_id: str, item_id: str | None = None
) -> int:
    predicate = (
        lambda key: key[0] == tenant_id
        and key[1] == external_source_id
        and (item_id is None or key[2] == item_id)
    )
    removed = thumbnail_cache.invalidate_where(predicate)
    removed += thumbnail_negative_cache.invalidate_where(predicate)
    return removed
