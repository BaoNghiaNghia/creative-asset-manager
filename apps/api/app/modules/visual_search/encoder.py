from __future__ import annotations

from PIL import Image

from app.modules.visual_search.contracts import (
    EmbeddingDescriptor,
    VisualEmbedding,
    VisualEncoder,
)


class EncoderContractViolationError(RuntimeError):
    """An isolated encoder returned data incompatible with its descriptor."""


class ValidatedVisualEncoder:
    """Validate a pluggable encoder without importing or loading an ML runtime."""

    def __init__(self, delegate: VisualEncoder):
        self._delegate = delegate
        self._descriptor = delegate.descriptor

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return self._descriptor

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        embedding = self._delegate.encode_image(image)
        if embedding.descriptor != self._descriptor:
            raise EncoderContractViolationError(
                "encoder returned an embedding with a different descriptor"
            )
        return embedding
