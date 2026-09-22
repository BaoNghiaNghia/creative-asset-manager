from __future__ import annotations

import pytest
from PIL import Image

from app.modules.visual_search.contracts import (
    EmbeddingDescriptor,
    VisualEmbedding,
    VisualEncoderQueueFullError,
    VisualEncoderQueueTimeoutError,
)
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


def test_http_encoder_sends_priority_without_client_side_busy_retries() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    request = httpx.Request("POST", "http://encoder.local/v1/encode-text")
    response = httpx.Response(200, json=_encoder_payload(), request=request)
    with patch(
        "app.modules.visual_search.encoder_client.httpx.post",
        return_value=response,
    ) as post:
        result = encoder.encode_text("black dress", priority="background")
    assert result.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert post.call_count == 1
    assert post.call_args.kwargs["json"] == {
        "text": "black dress",
        "priority": "background",
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("visual_encoder_queue_full", VisualEncoderQueueFullError),
        ("visual_encoder_queue_timeout", VisualEncoderQueueTimeoutError),
    ],
)
def test_http_encoder_preserves_bounded_queue_error(code, expected) -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    request = httpx.Request("POST", "http://encoder.local/v1/encode-text")
    response = httpx.Response(
        503,
        json={"detail": {"code": code, "retryable": True}},
        request=request,
    )
    with patch(
        "app.modules.visual_search.encoder_client.httpx.post",
        return_value=response,
    ):
        with pytest.raises(expected):
            encoder.encode_text("black dress")
