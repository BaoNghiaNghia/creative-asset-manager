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
