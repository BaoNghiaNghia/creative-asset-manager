from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.schema import NormalizedCrop, VisualSearchByAssetRequest, VisualSearchResponse


def descriptor() -> EmbeddingDescriptor:
    return EmbeddingDescriptor(
        encoder_name="test-encoder",
        encoder_revision="revision-1",
        embedding_schema_version="visual_embedding_v1",
        dimension=3,
        preprocess_version="rgb-224-v1",
    )


def test_embedding_contract_requires_matching_finite_dimensions() -> None:
    embedding = VisualEmbedding(descriptor(), (0.1, 0.2, 0.3))
    assert embedding.descriptor.similarity == "cosine"
    with pytest.raises(ValueError, match="dimension"):
        VisualEmbedding(descriptor(), (0.1, 0.2))
    with pytest.raises(ValueError, match="finite"):
        VisualEmbedding(descriptor(), (0.1, float("nan"), 0.3))


def test_normalized_crop_rejects_out_of_bounds_or_tiny_regions() -> None:
    assert NormalizedCrop(x=0.2, y=0.2, width=0.5, height=0.5).width == 0.5
    with pytest.raises(ValidationError):
        NormalizedCrop(x=0.8, y=0.2, width=0.3, height=0.5)
    with pytest.raises(ValidationError):
        NormalizedCrop(x=0.2, y=0.2, width=0.001, height=0.5)


def test_by_asset_request_reuses_search_filters_and_normalizes_text() -> None:
    request = VisualSearchByAssetRequest(
        asset_id="asset-1", text="  outdoor  ", filters={"media_kind": ["image"]}
    )
    assert request.text == "outdoor"
    assert request.filters.media_kind == ["image"]


def test_response_contract_never_exposes_embedding_or_similarity() -> None:
    response = VisualSearchResponse(query_kind="asset", items=[{"id": "asset-1"}])
    assert response.model_dump() == {
        "query_kind": "asset",
        "items": [{"id": "asset-1"}],
        "next_cursor": None,
        "has_more": False,
    }
