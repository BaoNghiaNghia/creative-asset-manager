from __future__ import annotations

from io import BytesIO

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

from app.modules.visual_search.encoder_client import (
    HttpVisualEncoder,
    _MAX_ENCODER_JPEG_BYTES,
)
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
    with patch.object(
        encoder._client,
        "post",
        return_value=response,
    ) as post:
        first = encoder.encode_text("black dress", priority="background")
        second = encoder.encode_text("black dress", priority="interactive")
    encoder.close()

    assert first.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert second.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"] == {
        "text": "black dress",
        "priority": "background",
    }
    assert post.call_args_list[1].kwargs["json"] == {
        "text": "black dress",
        "priority": "interactive",
    }


def test_http_encoder_sends_raw_jpeg_without_base64_json() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    request = httpx.Request(
        "POST",
        "http://encoder.local/v1/encode-image-bytes",
    )
    response = httpx.Response(200, json=_encoder_payload(), request=request)

    with patch.object(
        encoder._client,
        "post",
        return_value=response,
    ) as post:
        result = encoder.encode_image(
            Image.new("RGB", (32, 24), "navy"),
            priority="background",
        )
    encoder.close()

    assert result.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/v1/encode-image-bytes")
    assert post.call_args.kwargs["params"] == {"priority": "background"}
    assert post.call_args.kwargs["headers"] == {"Content-Type": "image/jpeg"}
    payload = post.call_args.kwargs["content"]
    assert isinstance(payload, bytes)
    with Image.open(BytesIO(payload)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (32, 24)


def test_http_encoder_bounds_large_detailed_image_payload() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    request = httpx.Request(
        "POST",
        "http://encoder.local/v1/encode-image-bytes",
    )
    response = httpx.Response(200, json=_encoder_payload(), request=request)
    image = Image.effect_noise((5000, 4000), 100).convert("RGB")

    with patch.object(encoder._client, "post", return_value=response) as post:
        result = encoder.encode_image(image, priority="background")
    encoder.close()

    assert result.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    payload = post.call_args.kwargs["content"]
    assert len(payload) <= _MAX_ENCODER_JPEG_BYTES
    with Image.open(BytesIO(payload)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.width <= image.width
        assert decoded.height <= image.height


def test_http_encoder_falls_back_to_legacy_base64_endpoint_on_404() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    raw_request = httpx.Request(
        "POST",
        "http://encoder.local/v1/encode-image-bytes",
    )
    legacy_request = httpx.Request(
        "POST",
        "http://encoder.local/v1/encode-image",
    )
    responses = [
        httpx.Response(404, request=raw_request),
        httpx.Response(200, json=_encoder_payload(), request=legacy_request),
    ]

    with patch.object(
        encoder._client,
        "post",
        side_effect=responses,
    ) as post:
        result = encoder.encode_image(
            Image.new("RGB", (16, 16), "white"),
            priority="interactive",
        )
    encoder.close()

    assert result.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR
    assert post.call_count == 2
    assert post.call_args_list[0].args[0].endswith("/v1/encode-image-bytes")
    assert post.call_args_list[1].args[0].endswith("/v1/encode-image")
    legacy_json = post.call_args_list[1].kwargs["json"]
    assert legacy_json["priority"] == "interactive"
    assert isinstance(legacy_json["image_base64"], str)
    assert legacy_json["image_base64"]


def test_http_encoder_close_releases_pooled_client() -> None:
    encoder = HttpVisualEncoder("http://encoder.local", internal_key="secret")
    with patch.object(encoder._client, "close") as close:
        encoder.close()
    close.assert_called_once_with()


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
    with patch.object(
        encoder._client,
        "post",
        return_value=response,
    ):
        with pytest.raises(expected):
            encoder.encode_text("black dress")
    encoder.close()
