from __future__ import annotations

import pytest
from PIL import Image

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.contracts import (
    EncoderContractViolationError,
    ValidatedVisualEncoder,
)


FIRST = EmbeddingDescriptor(
    encoder_name="one",
    encoder_revision="v1",
    embedding_schema_version="visual_embedding_v1",
    dimension=2,
    preprocess_version="rgb-v1",
)
SECOND = EmbeddingDescriptor(
    encoder_name="two",
    encoder_revision="v1",
    embedding_schema_version="visual_embedding_v1",
    dimension=2,
    preprocess_version="rgb-v1",
)


class GoodEncoder:
    descriptor = FIRST

    def encode_image(self, image):
        return VisualEmbedding(FIRST, (0.1, 0.2))


class InconsistentEncoder:
    descriptor = FIRST

    def encode_image(self, image):
        return VisualEmbedding(SECOND, (0.1, 0.2))


def test_validated_encoder_preserves_a_consistent_descriptor() -> None:
    encoder = ValidatedVisualEncoder(GoodEncoder())
    assert encoder.encode_image(Image.new("RGB", (1, 1))).descriptor == FIRST


def test_validated_encoder_rejects_descriptor_changes() -> None:
    encoder = ValidatedVisualEncoder(InconsistentEncoder())
    with pytest.raises(EncoderContractViolationError):
        encoder.encode_image(Image.new("RGB", (1, 1)))


from unittest.mock import patch

import httpx

from app.modules.visual_search.encoder_client import HttpVisualEncoder
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR


def _encoder_payload() -> dict[str, object]:
    descriptor = VISUAL_SEARCH_BASELINE_DESCRIPTOR
    return {
        "descriptor": {
            "encoder_name": descriptor.encoder_name,
            "encoder_revision": descriptor.encoder_revision,
            "embedding_schema_version": descriptor.embedding_schema_version,
            "dimension": descriptor.dimension,
            "preprocess_version": descriptor.preprocess_version,
            "similarity": descriptor.similarity,
        },
        "values": [0.0] * descriptor.dimension,
    }


def test_http_encoder_retries_transient_busy_response() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    request = httpx.Request("POST", "http://encoder.local/v1/encode-text")
    responses = [
        httpx.Response(503, headers={"Retry-After": "0"}, request=request),
        httpx.Response(200, json=_encoder_payload(), request=request),
    ]
    with patch("app.modules.visual_search.encoder_client.httpx.post", side_effect=responses) as post, \
         patch("app.modules.visual_search.encoder_client.sleep") as wait:
        result = encoder.encode_text("black dress")
    assert result.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert post.call_count == 2
    wait.assert_called_once_with(0.0)
