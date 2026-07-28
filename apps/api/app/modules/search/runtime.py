from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config, ElasticsearchV2Index


@dataclass(frozen=True, slots=True)
class _SuggestionCacheEntry:
    expires_at: float
    response: dict[str, Any]


class SearchSuggestionCache:
    """Small process-local cache; values are tenant- and provider-scoped."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str, str, str, int], _SuggestionCacheEntry] = OrderedDict()

    def get(self, key: tuple[str, str, str, str, int]) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None or entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry.response.copy()

    def put(self, key: tuple[str, str, str, str, int], response: dict[str, Any], *, ttl_seconds: int, max_entries: int) -> None:
        self._entries[key] = _SuggestionCacheEntry(self._clock() + ttl_seconds, response.copy())
        self._entries.move_to_end(key)
        while len(self._entries) > max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


class ApiSearchIndexPool:
    """Keeps HTTP connection pools alive for the lifetime of each API event loop."""

    def __init__(self) -> None:
        self._indexes: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[ElasticsearchV2Config, ElasticsearchV2Index]] = weakref.WeakKeyDictionary()
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()

    async def get(self, config: ElasticsearchV2Config) -> ElasticsearchV2Index:
        loop = asyncio.get_running_loop()
        indexes = self._indexes.setdefault(loop, {})
        current = indexes.get(config)
        if current is not None:
            return current
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            return indexes.setdefault(config, ElasticsearchV2Index(config))

    async def aclose_current_loop(self) -> None:
        loop = asyncio.get_running_loop()
        indexes = self._indexes.pop(loop, {})
        self._locks.pop(loop, None)
        for index in indexes.values():
            await index.aclose()

    def clear(self) -> None:
        """Test helper for clearing cached references before a fresh event loop."""
        self._indexes.clear()
        self._locks.clear()


API_SEARCH_INDEX_POOL = ApiSearchIndexPool()
SEARCH_SUGGESTION_CACHE = SearchSuggestionCache()
