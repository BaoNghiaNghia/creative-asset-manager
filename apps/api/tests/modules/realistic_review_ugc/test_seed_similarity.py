from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from PIL import Image

from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.realistic_review_ugc.seed_similarity import (
    SeedEmbeddingCache,
    SeedVisualAsset,
    build_seed_visual_signal,
    compute_seed_visual_signal,
    seed_visual_ranking_signal,
)
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding


DESCRIPTOR = EmbeddingDescriptor(
    encoder_name="test",
    encoder_revision="v1",
    embedding_schema_version="test-v1",
    dimension=2,
    preprocess_version="test-v1",
)


def _png(value: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (value, value, value)).save(output, format="PNG")
    return output.getvalue()


class _Encoder:
    descriptor = DESCRIPTOR

    def __init__(self) -> None:
        self.calls = 0

    def encode_image(self, image, *, priority="interactive"):
        del priority
        self.calls += 1
        value = image.resize((1, 1)).getpixel((0, 0))[0] / 255.0
        return VisualEmbedding(DESCRIPTOR, (value, 1.0 - value))


class _EncoderClient:
    def __init__(self) -> None:
        self.encoder = _Encoder()

    def get_encoder(self):
        return self.encoder


class _Storage:
    def __init__(self, rows: dict[str, bytes]) -> None:
        self.rows = rows
        self.calls = 0

    async def open_asset(self, input):
        self.calls += 1
        payload = self.rows[input.remote_file_id]

        async def body():
            yield payload

        async def close():
            return None

        return StoredAssetReadStream(
            body=body(),
            close=close,
            content_type="image/png",
            size_bytes=len(payload),
        )


def test_seed_visual_signal_is_bounded_and_combines_with_existing_ranking():
    signal = build_seed_visual_signal(
        positive_similarities=[1.0, 0.8],
        negative_similarities=[0.2],
    )
    assert signal["active"] is True
    assert signal["score"] == pytest.approx(0.7)
    assert signal["adjustment"] == pytest.approx(0.042)

    ranked = seed_visual_ranking_signal(
        signal={"seed_visual": signal},
        base_score=0.81,
    )
    assert ranked["ranking_score"] == pytest.approx(0.852)
    assert ranked["adjustment"] == pytest.approx(0.042)


def test_seed_visual_signal_can_be_active_with_neutral_adjustment():
    signal = build_seed_visual_signal(
        positive_similarities=[0.6],
        negative_similarities=[0.6],
    )
    ranked = seed_visual_ranking_signal(
        signal={"seed_visual": signal},
        base_score=0.7,
    )
    assert signal["active"] is True
    assert signal["adjustment"] == 0.0
    assert ranked["active"] is True
    assert ranked["ranking_score"] == 0.7


def test_seed_visual_signal_never_exceeds_ranking_bounds():
    boosted = seed_visual_ranking_signal(
        signal={"seed_visual": {"active": True, "adjustment": 99}},
        base_score=0.98,
    )
    downranked = seed_visual_ranking_signal(
        signal={"seed_visual": {"active": True, "adjustment": -99}},
        base_score=0.02,
    )
    assert boosted["ranking_score"] == 1.0
    assert boosted["adjustment"] == 0.06
    assert downranked["ranking_score"] == 0.0
    assert downranked["adjustment"] == -0.06


def test_seed_embeddings_are_cached_by_content_hash():
    storage = _Storage(
        {
            "positive": _png(230),
            "negative": _png(20),
        }
    )
    client = _EncoderClient()
    cache = SeedEmbeddingCache()
    seeds = [
        SeedVisualAsset(
            reference_asset_id="ref-positive",
            label="positive",
            content_hash="a" * 64,
            remote_file_id="positive",
            content_type="image/png",
            size_bytes=None,
        ),
        SeedVisualAsset(
            reference_asset_id="ref-negative",
            label="negative",
            content_hash="b" * 64,
            remote_file_id="negative",
            content_type="image/png",
            size_bytes=None,
        ),
    ]

    first = asyncio.run(
        compute_seed_visual_signal(
            tenant_id="tenant-a",
            candidate_bytes=_png(220),
            seeds=seeds,
            storage=storage,
            encoder_client=client,
            cache=cache,
        )
    )
    second = asyncio.run(
        compute_seed_visual_signal(
            tenant_id="tenant-a",
            candidate_bytes=_png(220),
            seeds=seeds,
            storage=storage,
            encoder_client=client,
            cache=cache,
        )
    )

    assert first["active"] is True
    assert first["positive_count"] == 1
    assert first["negative_count"] == 1
    assert first["adjustment"] > 0
    assert second == first
    assert storage.calls == 2
    # First run: candidate + two seeds. Second run: candidate only.
    assert client.encoder.calls == 4
