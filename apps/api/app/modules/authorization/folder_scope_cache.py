from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Callable, Mapping


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
