from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping


ParentMap = Mapping[str, tuple[str, ...]]


@dataclass
class _HierarchyLoadState:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


class ViewerFolderHierarchyCache:
    """Bounded, fail-closed cache of tenant/source parent maps."""

    def __init__(self, *, max_entries: int = 256, ttl_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("viewer folder hierarchy cache bounds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], tuple[float, ParentMap]] = OrderedDict()
        self._states: OrderedDict[tuple[str, str], _HierarchyLoadState] = OrderedDict()
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._states.clear()

    def invalidate(self, *, tenant_id: str, external_source_id: str | None) -> None:
        if external_source_id:
            with self._lock:
                self._entries.pop((str(tenant_id), str(external_source_id)), None)

    def get_or_load(self, *, tenant_id: str, external_source_id: str,
                    loader: Callable[[], ParentMap]) -> ParentMap | None:
        key = (str(tenant_id), str(external_source_id))
        with self._lock:
            cached = self._get_locked(key, self._clock())
            if cached is not None:
                return cached
            state = self._states.get(key)
            if state is None:
                state = _HierarchyLoadState()
                self._states[key] = state
            self._states.move_to_end(key)
            state.users += 1
            self._trim_states_locked()
        try:
            with state.lock:
                with self._lock:
                    cached = self._get_locked(key, self._clock())
                    if cached is not None:
                        return cached
                try:
                    loaded = loader()
                    value: ParentMap = MappingProxyType({
                        str(item_id): tuple(str(parent_id) for parent_id in parent_ids if parent_id)
                        for item_id, parent_ids in loaded.items()
                    })
                except Exception:
                    return None
                with self._lock:
                    self._entries[key] = (self._clock() + self.ttl_seconds, value)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
                return value
        finally:
            with self._lock:
                state.users -= 1
                self._trim_states_locked()

    def _get_locked(self, key: tuple[str, str], now: float) -> ParentMap | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= now:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def _trim_states_locked(self) -> None:
        if len(self._states) <= self.max_entries * 2:
            return
        for key, state in list(self._states.items()):
            if len(self._states) <= self.max_entries * 2:
                break
            if state.users == 0:
                self._states.pop(key, None)


viewer_folder_hierarchy_cache = ViewerFolderHierarchyCache()


class ViewerFolderRemoteParentCache:
    """Bounded async cache of immediate Google Drive parent IDs.

    This intentionally caches only provider hierarchy data, never an allow/deny
    decision. Each request still evaluates the caller's current folder scope.
    Concurrent requests for the same Drive item share one in-flight lookup.
    """

    def __init__(
        self,
        *,
        max_entries: int = 4096,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("viewer folder remote parent cache bounds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str, int, str], tuple[float, str]] = OrderedDict()
        self._inflight: dict[tuple[str, str, int, str], asyncio.Future[str | None]] = {}
        self._lock = asyncio.Lock()
        self._generation_lock = Lock()
        self._source_generations: dict[tuple[str, str], int] = {}

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()
        with self._generation_lock:
            self._source_generations.clear()

    def invalidate(self, *, tenant_id: str, external_source_id: str | None) -> None:
        """Synchronously invalidate after a source mutation or sync commit."""
        if not external_source_id:
            return
        prefix = (str(tenant_id), str(external_source_id))
        with self._generation_lock:
            self._source_generations[prefix] = self._source_generations.get(prefix, 0) + 1

    async def get_or_load(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        item_id: str,
        loader: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        source_key = (str(tenant_id), str(external_source_id))
        with self._generation_lock:
            generation = self._source_generations.get(source_key, 0)
        key = (*source_key, generation, str(item_id))
        async with self._lock:
            cached = self._get_locked(key, self._clock())
            if cached is not None:
                return cached
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                is_loader = True
            else:
                is_loader = False

        if not is_loader:
            return await asyncio.shield(future)

        try:
            parent_id = await loader()
            if parent_id:
                parent_id = str(parent_id)
                async with self._lock:
                    self._entries[key] = (self._clock() + self.ttl_seconds, parent_id)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            future.set_result(parent_id)
            return parent_id
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()
            raise
        finally:
            async with self._lock:
                self._inflight.pop(key, None)

    def _get_locked(self, key: tuple[str, str, int, str], now: float) -> str | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        expires_at, parent_id = cached
        if expires_at <= now:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return parent_id


viewer_folder_remote_parent_cache = ViewerFolderRemoteParentCache()
