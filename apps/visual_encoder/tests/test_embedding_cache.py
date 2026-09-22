from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR
from embedding_cache import (
    BoundedEmbeddingCache,
    image_cache_key,
    text_cache_key,
)


def _embedding(value: float) -> VisualEmbedding:
    return VisualEmbedding(
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
        (value,) + tuple(
            0.0
            for _ in range(
                VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension - 1
            )
        ),
    )


def test_bounded_embedding_cache_is_lru_and_observable() -> None:
    cache = BoundedEmbeddingCache[str](2)
    cache.put("a", _embedding(1.0))
    cache.put("b", _embedding(0.9))

    assert cache.get("a") is not None
    cache.put("c", _embedding(0.8))

    assert cache.get("b") is None
    snapshot = cache.snapshot()
    assert snapshot["entries"] == 2
    assert snapshot["hits_total"] == 1
    assert snapshot["misses_total"] == 1
    assert snapshot["evictions_total"] == 1


def test_cache_keys_bind_content_and_embedding_contract() -> None:
    image_a = Image.new("RGB", (4, 4), (255, 0, 0))
    image_b = Image.new("RGB", (4, 4), (0, 0, 255))

    first = image_cache_key(image_a, VISUAL_SEARCH_ACTIVE_DESCRIPTOR)
    same = image_cache_key(image_a.copy(), VISUAL_SEARCH_ACTIVE_DESCRIPTOR)
    different = image_cache_key(image_b, VISUAL_SEARCH_ACTIVE_DESCRIPTOR)

    assert first == same
    assert first != different
    assert first.embedding_schema_version == "visual_embedding_v2"
    assert first.preprocess_version == VISUAL_SEARCH_ACTIVE_DESCRIPTOR.preprocess_version

    text_a = text_cache_key(
        "  blue floral embroidery  ",
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
    )
    text_b = text_cache_key(
        "blue floral embroidery",
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
    )
    text_c = text_cache_key(
        "Blue floral embroidery",
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
    )
    assert text_a == text_b
    assert text_a != text_c
