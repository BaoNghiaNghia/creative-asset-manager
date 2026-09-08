"""Foundation contracts for optional, tenant-safe visual search."""

from .contracts import EmbeddingDescriptor, VisualEmbedding, VisualEncoder
from .service import VisualSearchService

__all__ = [
    "EmbeddingDescriptor",
    "VisualEmbedding",
    "VisualEncoder",
    "VisualSearchService",
]
