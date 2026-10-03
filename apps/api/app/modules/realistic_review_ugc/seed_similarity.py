from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import sqrt
from typing import Any

from app.domain.providers.contracts import (
    AssetStorageProvider,
    OpenStoredAssetInput,
    StorageProviderError,
)
from app.modules.visual_search.contracts import (
    VisualEmbedding,
    VisualEncoderUnavailableError,
)
from app.modules.visual_search.preprocess import (
    VisualImagePreparationError,
    decode_visual_image,
)


SEED_VISUAL_PROFILE = "realistic-person-ugc"
SEED_VISUAL_MAX_PER_LABEL = 4
SEED_VISUAL_MAX_BYTES = 25_000_000
SEED_VISUAL_MAX_RANKING_ADJUSTMENT = 0.06
SEED_VISUAL_CACHE_SIZE = 64

# Local-first rejection is deliberately conservative. The candidate must match
# at least two negative seeds. When no positive comparator exists, similarity
# must be exceptionally high. When positive seeds exist, the negative match
# must still be strong and beat the positive match by a wide margin.
SEED_VISUAL_LOCAL_REJECT_MIN_NEGATIVE_COUNT = 2
SEED_VISUAL_LOCAL_REJECT_NEGATIVE_ONLY_MIN_SIMILARITY = 0.88
SEED_VISUAL_LOCAL_REJECT_MIN_NEGATIVE_SIMILARITY = 0.76
SEED_VISUAL_LOCAL_REJECT_MIN_MARGIN = 0.18


@dataclass(frozen=True, slots=True)
class SeedVisualAsset:
    reference_asset_id: str
    label: str
    content_hash: str
    remote_file_id: str
    content_type: str | None
    size_bytes: int | None


class SeedEmbeddingCache:
    def __init__(self, max_items: int = SEED_VISUAL_CACHE_SIZE) -> None:
        self.max_items = max(1, int(max_items))
        self._rows: OrderedDict[str, VisualEmbedding] = OrderedDict()

    def get(self, content_hash: str) -> VisualEmbedding | None:
        row = self._rows.get(content_hash)
        if row is not None:
            self._rows.move_to_end(content_hash)
        return row

    def put(self, content_hash: str, embedding: VisualEmbedding) -> None:
        self._rows[content_hash] = embedding
        self._rows.move_to_end(content_hash)
        while len(self._rows) > self.max_items:
            self._rows.popitem(last=False)


def cosine_similarity(left: VisualEmbedding, right: VisualEmbedding) -> float:
    if left.descriptor != right.descriptor:
        raise ValueError("seed embeddings use different visual schemas")
    dot = sum(a * b for a, b in zip(left.values, right.values, strict=True))
    left_norm = sqrt(sum(value * value for value in left.values))
    right_norm = sqrt(sum(value * value for value in right.values))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _top_similarity(values: list[float]) -> float | None:
    if not values:
        return None
    # Average the two strongest matches when available so one accidental
    # nearest neighbour cannot dominate the ranking signal.
    ranked = sorted(values, reverse=True)[:2]
    return sum(ranked) / len(ranked)


def build_seed_visual_signal(
    *,
    positive_similarities: list[float],
    negative_similarities: list[float],
    profile_key: str = SEED_VISUAL_PROFILE,
) -> dict[str, Any]:
    positive = _top_similarity(positive_similarities)
    negative = _top_similarity(negative_similarities)
    if positive is None and negative is None:
        return {
            "active": False,
            "profile_key": profile_key,
            "positive_count": 0,
            "negative_count": 0,
            "positive_similarity": None,
            "negative_similarity": None,
            "score": 0.0,
            "adjustment": 0.0,
        }

    raw = (positive or 0.0) - (negative or 0.0)
    raw = max(-1.0, min(1.0, raw))
    adjustment = max(
        -SEED_VISUAL_MAX_RANKING_ADJUSTMENT,
        min(SEED_VISUAL_MAX_RANKING_ADJUSTMENT, raw * SEED_VISUAL_MAX_RANKING_ADJUSTMENT),
    )
    return {
        "active": bool(positive_similarities or negative_similarities),
        "profile_key": profile_key,
        "positive_count": len(positive_similarities),
        "negative_count": len(negative_similarities),
        "positive_similarity": round(positive, 4) if positive is not None else None,
        "negative_similarity": round(negative, 4) if negative is not None else None,
        "score": round(raw, 4),
        "adjustment": round(adjustment, 4),
    }


def high_confidence_negative_seed_gate(
    signal: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return details only for a very high-confidence local negative match."""
    if not isinstance(signal, dict) or not bool(signal.get("active")):
        return None
    try:
        negative_count = int(signal.get("negative_count") or 0)
        positive_count = int(signal.get("positive_count") or 0)
        negative_similarity = float(signal.get("negative_similarity"))
    except (TypeError, ValueError):
        return None
    if negative_count < SEED_VISUAL_LOCAL_REJECT_MIN_NEGATIVE_COUNT:
        return None

    positive_similarity: float | None = None
    if positive_count > 0:
        try:
            positive_similarity = float(signal.get("positive_similarity"))
        except (TypeError, ValueError):
            return None
        margin = negative_similarity - positive_similarity
        if (
            negative_similarity < SEED_VISUAL_LOCAL_REJECT_MIN_NEGATIVE_SIMILARITY
            or margin < SEED_VISUAL_LOCAL_REJECT_MIN_MARGIN
        ):
            return None
        mode = "negative_over_positive"
    else:
        margin = negative_similarity
        if negative_similarity < SEED_VISUAL_LOCAL_REJECT_NEGATIVE_ONLY_MIN_SIMILARITY:
            return None
        mode = "negative_only"

    return {
        "version": 1,
        "gate": "seed_visual_high_confidence_negative",
        "mode": mode,
        "negative_count": negative_count,
        "positive_count": positive_count,
        "negative_similarity": round(negative_similarity, 4),
        "positive_similarity": (
            round(positive_similarity, 4) if positive_similarity is not None else None
        ),
        "margin": round(margin, 4),
    }


def seed_visual_ranking_signal(
    *,
    signal: dict[str, Any] | None,
    base_score: float | None,
) -> dict[str, Any]:
    seed = (
        dict(signal.get("seed_visual"))
        if isinstance(signal, dict) and isinstance(signal.get("seed_visual"), dict)
        else {}
    )
    active = bool(seed.get("active"))
    try:
        adjustment = float(seed.get("adjustment") or 0.0) if active else 0.0
    except (TypeError, ValueError):
        adjustment = 0.0
    adjustment = max(
        -SEED_VISUAL_MAX_RANKING_ADJUSTMENT,
        min(SEED_VISUAL_MAX_RANKING_ADJUSTMENT, adjustment),
    )
    ranking_score = (
        round(max(0.0, min(1.0, float(base_score) + adjustment)), 4)
        if base_score is not None
        else None
    )
    return {
        "active": active,
        "ranking_score": ranking_score,
        "adjustment": round(adjustment, 4),
        "score": seed.get("score"),
        "positive_similarity": seed.get("positive_similarity"),
        "negative_similarity": seed.get("negative_similarity"),
        "positive_count": int(seed.get("positive_count") or 0),
        "negative_count": int(seed.get("negative_count") or 0),
        "profile_key": seed.get("profile_key"),
    }


async def _read_seed_bytes(
    *,
    storage: AssetStorageProvider,
    tenant_id: str,
    seed: SeedVisualAsset,
) -> bytes:
    if seed.size_bytes is not None and seed.size_bytes > SEED_VISUAL_MAX_BYTES:
        raise ValueError("seed reference exceeds the visual byte limit")
    stream = await storage.open_asset(
        OpenStoredAssetInput(
            tenant_id=tenant_id,
            asset_id=seed.reference_asset_id,
            remote_file_id=seed.remote_file_id,
            content_type=seed.content_type,
            size_bytes=seed.size_bytes,
        )
    )
    total = 0
    chunks: list[bytes] = []
    try:
        async for chunk in stream.body:
            total += len(chunk)
            if total > SEED_VISUAL_MAX_BYTES:
                raise ValueError("seed reference exceeds the visual byte limit")
            chunks.append(chunk)
    finally:
        await stream.close()
    return b"".join(chunks)


async def compute_seed_visual_signal(
    *,
    tenant_id: str,
    candidate_bytes: bytes,
    seeds: list[SeedVisualAsset],
    storage: AssetStorageProvider | None,
    encoder_client: Any,
    cache: SeedEmbeddingCache,
    profile_key: str = SEED_VISUAL_PROFILE,
) -> dict[str, Any]:
    if not seeds:
        return build_seed_visual_signal(
            positive_similarities=[],
            negative_similarities=[],
            profile_key=profile_key,
        )
    if storage is None or encoder_client is None:
        return {
            **build_seed_visual_signal(
                positive_similarities=[],
                negative_similarities=[],
                profile_key=profile_key,
            ),
            "reason": "visual_encoder_unavailable",
        }

    try:
        candidate = decode_visual_image(candidate_bytes)
        encoder = encoder_client.get_encoder()
        candidate_embedding = encoder.encode_image(
            candidate.image,
            priority="background",
        )
    except (VisualImagePreparationError, VisualEncoderUnavailableError, ValueError):
        return {
            **build_seed_visual_signal(
                positive_similarities=[],
                negative_similarities=[],
                profile_key=profile_key,
            ),
            "reason": "candidate_embedding_unavailable",
        }

    positive_similarities: list[float] = []
    negative_similarities: list[float] = []
    for seed in seeds:
        if seed.label not in {"positive", "negative"}:
            continue
        embedding = cache.get(seed.content_hash)
        if (
            embedding is not None
            and embedding.descriptor != candidate_embedding.descriptor
        ):
            embedding = None
        if embedding is None:
            try:
                content = await _read_seed_bytes(
                    storage=storage,
                    tenant_id=tenant_id,
                    seed=seed,
                )
                prepared = decode_visual_image(content)
                embedding = encoder.encode_image(
                    prepared.image,
                    priority="background",
                )
                cache.put(seed.content_hash, embedding)
            except (
                StorageProviderError,
                VisualImagePreparationError,
                VisualEncoderUnavailableError,
                ValueError,
            ):
                continue
        try:
            similarity = cosine_similarity(candidate_embedding, embedding)
        except ValueError:
            continue
        if seed.label == "positive":
            positive_similarities.append(similarity)
        else:
            negative_similarities.append(similarity)

    return build_seed_visual_signal(
        positive_similarities=positive_similarities,
        negative_similarities=negative_similarities,
        profile_key=profile_key,
    )
