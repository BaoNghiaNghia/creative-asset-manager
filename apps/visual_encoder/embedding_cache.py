from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding


K = TypeVar("K")


@dataclass(frozen=True, slots=True)
class EmbeddingCacheKey:
    kind: str
    content_sha256: str
    encoder_name: str
    encoder_revision: str
    embedding_schema_version: str
    preprocess_version: str


def image_cache_key(
    request_sha256: str,
    descriptor: EmbeddingDescriptor,
) -> EmbeddingCacheKey:
    digest = request_sha256.strip().casefold()
    if (
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("request_sha256 must be a lowercase-compatible SHA-256 hex digest")
    return EmbeddingCacheKey(
        "image",
        digest,
        descriptor.encoder_name,
        descriptor.encoder_revision,
        descriptor.embedding_schema_version,
        descriptor.preprocess_version,
    )


def text_cache_key(
    text: str,
    descriptor: EmbeddingDescriptor,
) -> EmbeddingCacheKey:
    value = text.strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return EmbeddingCacheKey(
        "text",
        digest,
        descriptor.encoder_name,
        descriptor.encoder_revision,
        descriptor.embedding_schema_version,
        descriptor.preprocess_version,
    )


class BoundedEmbeddingCache(Generic[K]):
    """Thread-safe count-bounded LRU for derived embeddings only."""

    def __init__(self, max_entries: int) -> None:
        if max_entries < 0:
            raise ValueError("max_entries must be non-negative")
        self._max_entries = max_entries
        self._items: OrderedDict[K, VisualEmbedding] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: K) -> VisualEmbedding | None:
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self._misses += 1
                return None
            self._items.move_to_end(key)
            self._hits += 1
            return value

    def peek(self, key: K) -> VisualEmbedding | None:
        """Internal duplicate suppression without affecting request hit/miss metrics."""
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: K, value: VisualEmbedding) -> None:
        with self._lock:
            if self._max_entries == 0:
                return
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._items),
                "max_entries": self._max_entries,
                "hits_total": self._hits,
                "misses_total": self._misses,
                "evictions_total": self._evictions,
            }
