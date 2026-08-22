from datetime import datetime, timedelta, timezone

from app.common.cache import BoundedTTLCache
from app.modules.ai_operations.cache import (
    ai_operations_caches,
    cached_ai_operations_read,
    filters_cache_key,
)
from app.modules.ai_operations.schema import AiOperationsFilters


def _filters(tenant="tenant-a", start_day=1):
    start = datetime(2026, 8, start_day, tzinfo=timezone.utc)
    return AiOperationsFilters(
        tenant_id=tenant,
        from_at=start,
        to_at=start + timedelta(days=1),
        provider="gemini",
        model="flash",
    )


def test_same_query_cached_and_dimensions_are_isolated():
    for cache in ai_operations_caches.values():
        cache.clear()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"calls": calls, "items": []}

    first = cached_ai_operations_read(
        "jobs", filters_cache_key(_filters()) + (1, 25), load
    )
    second = cached_ai_operations_read(
        "jobs", filters_cache_key(_filters()) + (1, 25), load
    )
    assert first == second
    assert calls == 1

    cached_ai_operations_read(
        "jobs", filters_cache_key(_filters()) + (2, 25), load
    )
    cached_ai_operations_read(
        "jobs", filters_cache_key(_filters("tenant-b")) + (1, 25), load
    )
    cached_ai_operations_read(
        "jobs", filters_cache_key(_filters(start_day=2)) + (1, 25), load
    )
    assert calls == 4


def test_ai_operations_ttl_expiry_refreshes():
    now = [100.0]
    original = ai_operations_caches["summary"]
    ai_operations_caches["summary"] = BoundedTTLCache(
        max_entries=8, ttl_seconds=5, clock=lambda: now[0]
    )
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"value": calls}

    try:
        key = filters_cache_key(_filters())
        assert cached_ai_operations_read("summary", key, load)["value"] == 1
        now[0] += 6
        assert cached_ai_operations_read("summary", key, load)["value"] == 2
    finally:
        ai_operations_caches["summary"] = original
