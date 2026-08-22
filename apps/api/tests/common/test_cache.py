from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.common.cache import AsyncSingleFlightTTLCache, BoundedTTLCache, ByteSizeTTLCache


def test_bounded_ttl_cache_hit_miss_expiry_lru_and_invalidation():
    now = [0.0]
    cache = BoundedTTLCache[str, str](
        max_entries=2, ttl_seconds=10, clock=lambda: now[0],
    )
    assert cache.get("missing") is None
    cache.put("a", "A")
    cache.put("b", "B")
    assert cache.get("a") == "A"
    cache.put("c", "C")
    assert cache.get("b") is None
    assert cache.get("a") == "A"
    cache.invalidate("a")
    assert cache.get("a") is None
    now[0] = 11
    assert cache.get("c") is None
    assert cache.metrics().eviction == 1


def test_bounded_ttl_cache_is_thread_safe():
    cache = BoundedTTLCache[int, int](max_entries=64, ttl_seconds=60)
    errors = []

    def worker(offset: int):
        try:
            for value in range(500):
                key = (value + offset) % 64
                cache.put(key, value)
                cache.get(key)
        except Exception as exc:  # pragma: no cover - assertion captures failures
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(cache) <= 64


def test_byte_size_cache_evicts_by_memory_budget():
    cache = ByteSizeTTLCache[str, bytes](
        max_entries=10, max_bytes=6, ttl_seconds=60, size_of=len,
    )
    cache.put("a", b"1234")
    cache.put("b", b"5678")
    assert cache.get("a") is None
    assert cache.get("b") == b"5678"
    assert cache.total_bytes == 4
    cache.put("too-large", b"1234567")
    assert cache.get("too-large") is None


def test_singleflight_twenty_callers_load_once():
    async def scenario():
        cache = AsyncSingleFlightTTLCache(
            BoundedTTLCache[str, str](max_entries=16, ttl_seconds=60)
        )
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return "value"

        values = await asyncio.gather(*(cache.get_or_load("same", loader) for _ in range(20)))
        assert values == ["value"] * 20
        assert calls == 1
        assert cache.metrics().singleflight_join == 19

    asyncio.run(scenario())


def test_singleflight_failure_does_not_poison_cache():
    async def scenario():
        cache = AsyncSingleFlightTTLCache(
            BoundedTTLCache[str, str](max_entries=16, ttl_seconds=60)
        )
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary")
            return "recovered"

        with pytest.raises(RuntimeError):
            await cache.get_or_load("same", loader)
        assert await cache.get_or_load("same", loader) == "recovered"
        assert calls == 2

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_shared_producer():
    async def scenario():
        cache = AsyncSingleFlightTTLCache(
            BoundedTTLCache[str, str](max_entries=16, ttl_seconds=60)
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def loader():
            started.set()
            await release.wait()
            return "value"

        cancelled = asyncio.create_task(cache.get_or_load("same", loader))
        await started.wait()
        survivor = asyncio.create_task(cache.get_or_load("same", loader))
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()
        assert await survivor == "value"
        assert await cache.get_or_load("same", loader) == "value"

    asyncio.run(scenario())
