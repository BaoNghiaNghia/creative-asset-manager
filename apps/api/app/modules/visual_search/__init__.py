"""Foundation contracts for optional, tenant-safe visual search."""

from .contracts import EmbeddingDescriptor, VisualEmbedding, VisualEncoder

__all__ = [
    "EmbeddingDescriptor",
    "VisualEmbedding",
    "VisualEncoder",
    "VisualSearchService",
]


def __getattr__(name: str):
    if name == "VisualSearchService":
        from .service import VisualSearchService

        return VisualSearchService
    raise AttributeError(name)
