from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualSearchHit


@dataclass(frozen=True, slots=True)
class VisualRankingWeights:
    """Explicit, bounded V1 ranking controls."""

    image: float = 0.82
    text: float = 0.18
    max_per_source: int = 2

    def __post_init__(self) -> None:
        if self.image <= 0 or self.text < 0 or self.image + self.text <= 0:
            raise ValueError("visual ranking weights must include image weight")
        if self.max_per_source < 1:
            raise ValueError("max_per_source must be at least one")


def fuse_embeddings(
    image: VisualEmbedding,
    text: VisualEmbedding | None,
    *,
    weights: VisualRankingWeights,
) -> VisualEmbedding:
    """Blend image and optional text in the shared SigLIP space, then normalize."""
    if text is None:
        return image
    if text.descriptor != image.descriptor:
        raise ValueError("image and text embeddings must share a descriptor")
    values = tuple(
        weights.image * left + weights.text * right
        for left, right in zip(image.values, text.values, strict=True)
    )
    magnitude = sqrt(sum(value * value for value in values))
    if magnitude == 0:
        raise ValueError("fused visual embedding must be non-zero")
    return VisualEmbedding(image.descriptor, tuple(value / magnitude for value in values))


def diversify_hits(
    hits: Iterable[VisualSearchHit], *, weights: VisualRankingWeights
) -> list[VisualSearchHit]:
    """Suppress exact duplicates and avoid source-heavy result pages."""
    results: list[VisualSearchHit] = []
    seen_content: set[str] = set()
    source_counts: dict[str, int] = {}
    for hit in hits:
        if hit.content_sha256 in seen_content:
            continue
        source_key = hit.source_id or ""
        if source_key and source_counts.get(source_key, 0) >= weights.max_per_source:
            continue
        seen_content.add(hit.content_sha256)
        if source_key:
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
        results.append(hit)
    return results
