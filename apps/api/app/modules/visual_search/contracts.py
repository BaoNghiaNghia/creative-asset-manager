from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image

SimilarityMetric = Literal["cosine"]


@dataclass(frozen=True, slots=True)
class EmbeddingDescriptor:
    """Versioned, immutable contract for one visual embedding schema."""

    encoder_name: str
    encoder_revision: str
    embedding_schema_version: str
    dimension: int
    preprocess_version: str
    similarity: SimilarityMetric = "cosine"

    def __post_init__(self) -> None:
        for name in (
            "encoder_name",
            "encoder_revision",
            "embedding_schema_version",
            "preprocess_version",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.dimension <= 0:
            raise ValueError("dimension must be positive")
        if self.similarity != "cosine":
            raise ValueError("only cosine similarity is supported by the V1 contract")


@dataclass(frozen=True, slots=True)
class VisualEmbedding:
    """A vector coupled to the descriptor required to interpret it safely."""

    descriptor: EmbeddingDescriptor
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != self.descriptor.dimension:
            raise ValueError("embedding dimension does not match its descriptor")
        if not all(isfinite(value) for value in self.values):
            raise ValueError("embedding values must be finite")


@runtime_checkable
class VisualEncoder(Protocol):
    """Image-only encoder boundary; implementations live outside FastAPI."""

    @property
    def descriptor(self) -> EmbeddingDescriptor: ...

    def encode_image(self, image: "Image.Image") -> VisualEmbedding: ...


@runtime_checkable
class TextVisualEncoder(VisualEncoder, Protocol):
    """Optional shared text/image encoder capability for a future VS-09."""

    def encode_text(self, text: str) -> VisualEmbedding: ...


class VisualEncoderUnavailableError(RuntimeError):
    """Raised when the isolated encoder has no capacity or is unavailable."""

    code = "visual_encoder_unavailable"


class EncoderContractViolationError(RuntimeError):
    """An encoder returned data incompatible with its declared descriptor."""


class ValidatedVisualEncoder:
    """Validate a pluggable encoder without importing an ML runtime."""

    def __init__(self, delegate: VisualEncoder):
        self._delegate = delegate
        self._descriptor = delegate.descriptor

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return self._descriptor

    def encode_image(self, image: "Image.Image") -> VisualEmbedding:
        embedding = self._delegate.encode_image(image)
        if embedding.descriptor != self._descriptor:
            raise EncoderContractViolationError(
                "encoder returned an embedding with a different descriptor"
            )
        return embedding
