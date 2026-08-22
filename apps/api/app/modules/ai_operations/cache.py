from __future__ import annotations

from copy import deepcopy
from collections.abc import Awaitable
from typing import Callable, TypeVar

from app.common.cache import BoundedTTLCache
from app.modules.ai_operations.schema import AiOperationsFilters

V = TypeVar("V")

AI_OPERATIONS_TTLS = {
    "summary": 5,
    "daily": 10,
    "providers": 5,
    "failures": 5,
    "usage": 5,
    "jobs": 3,
    "pipeline": 3,
    "media-dashboard": 3,
}

ai_operations_caches = {
    name: BoundedTTLCache[tuple[object, ...], object](
        max_entries=512, ttl_seconds=ttl
    )
    for name, ttl in AI_OPERATIONS_TTLS.items()
}


def filters_cache_key(filters: AiOperationsFilters) -> tuple[object, ...]:
    return (
        filters.tenant_id,
        filters.from_at.isoformat(),
        filters.to_at.isoformat(),
        filters.provider or "",
        filters.model or "",
        filters.processing_mode or "",
        filters.metadata_profile or "",
        filters.status or "",
        filters.source_provider or "",
    )


def cached_ai_operations_read(
    name: str,
    key: tuple[object, ...],
    loader: Callable[[], V],
) -> V:
    cache = ai_operations_caches[name]
    cached = cache.get(key)
    if cached is not None:
        return deepcopy(cached)
    cache._record("load")
    try:
        value = loader()
    except BaseException:
        cache._record("load_error")
        raise
    cache.put(key, deepcopy(value))
    return value


def invalidate_ai_operations(tenant_id: str | None = None) -> int:
    removed = 0
    for cache in ai_operations_caches.values():
        if tenant_id is None:
            removed += len(cache)
            cache.clear()
        else:
            removed += cache.invalidate_where(
                lambda key: bool(key) and key[0] == tenant_id
            )
    return removed


async def cached_ai_operations_async(
    name: str,
    key: tuple[object, ...],
    loader: Callable[[], Awaitable[V]],
) -> V:
    cache = ai_operations_caches[name]
    cached = cache.get(key)
    if cached is not None:
        return deepcopy(cached)
    cache._record("load")
    try:
        value = await loader()
    except BaseException:
        cache._record("load_error")
        raise
    cache.put(key, deepcopy(value))
    return value
