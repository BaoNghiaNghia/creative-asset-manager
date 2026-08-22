from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Awaitable, Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class CacheMetrics:
    hit: int
    miss: int
    eviction: int
    load: int
    load_error: int
    singleflight_join: int


class BoundedTTLCache(Generic[K, V]):
    """Small thread-safe LRU/TTL cache with explicit scoped invalidation."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("cache bounds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = RLock()
        self._counters = {
            "hit": 0, "miss": 0, "eviction": 0, "load": 0,
            "load_error": 0, "singleflight_join": 0,
        }

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._counters["miss"] += 1
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                self._entries.pop(key, None)
                self._counters["miss"] += 1
                return None
            self._entries.move_to_end(key)
            self._counters["hit"] += 1
            return value

    def put(self, key: K, value: V, *, ttl_seconds: float | None = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("cache TTL must be positive")
        with self._lock:
            self._entries[key] = (self._clock() + ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._counters["eviction"] += 1

    def invalidate(self, key: K) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_where(self, predicate: Callable[[K], bool]) -> int:
        with self._lock:
            keys = [key for key in self._entries if predicate(key)]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def metrics(self) -> CacheMetrics:
        with self._lock:
            return CacheMetrics(**self._counters)

    def _record(self, event: str) -> None:
        with self._lock:
            self._counters[event] += 1


class ByteSizeTTLCache(BoundedTTLCache[K, V]):
    """LRU/TTL cache bounded by both item count and approximate value bytes."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        ttl_seconds: float,
        size_of: Callable[[V], int],
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_bytes < 1:
            raise ValueError("cache byte bound must be positive")
        super().__init__(max_entries=max_entries, ttl_seconds=ttl_seconds, clock=clock)
        self.max_bytes = max_bytes
        self._size_of = size_of
        self._total_bytes = 0

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._counters["miss"] += 1
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                self._remove_locked(key)
                self._counters["miss"] += 1
                return None
            self._entries.move_to_end(key)
            self._counters["hit"] += 1
            return value

    def put(self, key: K, value: V, *, ttl_seconds: float | None = None) -> None:
        size = max(0, int(self._size_of(value)))
        if size > self.max_bytes:
            return
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("cache TTL must be positive")
        with self._lock:
            self._remove_locked(key)
            self._entries[key] = (self._clock() + ttl, value)
            self._entries.move_to_end(key)
            self._total_bytes += size
            while len(self._entries) > self.max_entries or self._total_bytes > self.max_bytes:
                oldest = next(iter(self._entries))
                self._remove_locked(oldest)
                self._counters["eviction"] += 1

    def invalidate(self, key: K) -> None:
        with self._lock:
            self._remove_locked(key)

    def invalidate_where(self, predicate: Callable[[K], bool]) -> int:
        with self._lock:
            keys = [key for key in self._entries if predicate(key)]
            for key in keys:
                self._remove_locked(key)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def _remove_locked(self, key: K) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= max(0, int(self._size_of(entry[1])))


class AsyncSingleFlightTTLCache(Generic[K, V]):
    """Async request coalescing over a bounded cache.

    Producers run in their own task so cancelling one waiter does not cancel
    the shared load for other callers.
    """

    def __init__(self, cache: BoundedTTLCache[K, V]):
        self.cache = cache
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        *,
        ttl_seconds: float | None = None,
    ) -> V:
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._produce(key, loader, ttl_seconds))
                self._inflight[key] = task
            else:
                self.cache._record("singleflight_join")
        return await asyncio.shield(task)

    async def _produce(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        ttl_seconds: float | None,
    ) -> V:
        self.cache._record("load")
        try:
            value = await loader()
            self.cache.put(key, value, ttl_seconds=ttl_seconds)
            return value
        except BaseException:
            self.cache._record("load_error")
            raise
        finally:
            async with self._lock:
                current = self._inflight.get(key)
                if current is asyncio.current_task():
                    self._inflight.pop(key, None)

    def invalidate(self, key: K) -> None:
        self.cache.invalidate(key)

    def invalidate_where(self, predicate: Callable[[K], bool]) -> int:
        return self.cache.invalidate_where(predicate)

    def clear(self) -> None:
        self.cache.clear()

    def metrics(self) -> CacheMetrics:
        return self.cache.metrics()
