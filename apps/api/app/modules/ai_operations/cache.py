from __future__ import annotations

from copy import deepcopy
from collections.abc import Awaitable
from typing import Callable, TypeVar

from app.common.cache import BoundedTTLCache
from app.modules.ai_operations.schema import AiOperationsFilters

V = TypeVar("V")

AI_OPERATIONS_TTLS = {
    # A 20-second fallback TTL spans one 15-second auto-refresh while tenant
    # mutations continue to invalidate immediately. Jobs stay near-live.
    "summary": 20,
    "daily": 20,
    "providers": 20,
    "failures": 20,
    "usage": 20,
    "jobs": 3,
    # These snapshots aggregate a large historical data set. They are refreshed
    # by the UI often, while operational mutations invalidate this cache, so a
    # one-minute fallback TTL avoids repeating the same expensive snapshot.
    "pipeline": 60,
    "media-dashboard": 60,
}

ai_operations_caches = {
    name: BoundedTTLCache[tuple[object, ...], object](
        max_entries=512, ttl_seconds=ttl
    )
    for name, ttl in AI_OPERATIONS_TTLS.items()
}


def _time_bucket(value, seconds: int = 30) -> str:
    # Browser requests include a fresh `to` timestamp on each refresh. Cache
    # identity may be slightly coarser than the requested operational window,
    # while the first request's exact window remains the one executed.
    return value.replace(second=(value.second // seconds) * seconds, microsecond=0).isoformat()


def filters_cache_key(filters: AiOperationsFilters) -> tuple[object, ...]:
    return (
        filters.tenant_id,
        _time_bucket(filters.from_at),
        _time_bucket(filters.to_at),
        filters.provider or "",
        filters.model or "",
        filters.processing_mode or "",
        filters.metadata_profile or "",
        filters.status or "",
        filters.source_provider or "",
        filters.job_type or "",
    )




def media_dashboard_cache_key(filters: AiOperationsFilters) -> tuple[object, ...]:
    """Use a short, minute-bucketed cache for live dashboard refreshes.

    The dashboard reports aggregate operational state, so sub-minute changes to
    the requested time window do not warrant recomputing its expensive
    historical aggregates on every UI refresh.
    """
    return (
        filters.tenant_id,
        filters.from_at.replace(second=0, microsecond=0).isoformat(),
        filters.to_at.replace(second=0, microsecond=0).isoformat(),
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
