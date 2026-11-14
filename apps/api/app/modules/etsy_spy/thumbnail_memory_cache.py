"""Small tenant-isolated *memory-only* cache for immutable Design thumbnails.

Never persists Drive bytes to VPS storage; never caches originals, failures or
authorization. Callers MUST check tenant+Picked ACL before looking up an item.
"""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from time import monotonic


class ThumbnailMemoryCache:
    def __init__(
        self, *, max_bytes: int = 16 * 1024 * 1024,
        max_item_bytes: int = 512 * 1024, ttl_seconds: int = 900,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_item_bytes = max_item_bytes
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, bytes]] = OrderedDict()
        self._size = 0
        self._lock = Lock()

    def get(self, key: tuple[str, str, str]) -> bytes | None:
        with self._lock:
            item = self._entries.pop(key, None)
            if item is None:
                return None
            expires_at, payload = item
            if monotonic() >= expires_at:
                self._size -= len(payload)
                return None
            self._entries[key] = item  # LRU
            return payload

    def put(self, key: tuple[str, str, str], payload: bytes) -> None:
        if not 0 < len(payload) <= self.max_item_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size -= len(previous[1])
            while self._entries and self._size + len(payload) > self.max_bytes:
                _, (_, evicted) = self._entries.popitem(last=False)
                self._size -= len(evicted)
            if len(payload) > self.max_bytes:
                return
            self._entries[key] = (monotonic() + self.ttl_seconds, payload)
            self._size += len(payload)

    @property
    def bytes_used(self) -> int:
        with self._lock:
            return self._size
