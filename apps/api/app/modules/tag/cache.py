from __future__ import annotations

from app.common.cache import BoundedTTLCache
from app.modules.tag.schema import Tag

tag_catalog_cache: BoundedTTLCache[str, tuple[Tag, ...]] = BoundedTTLCache(
    max_entries=8, ttl_seconds=60
)


def invalidate_tag_catalog() -> None:
    tag_catalog_cache.clear()
