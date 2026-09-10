"""Versioned Visual Search compatibility metadata with no ML runtime dependency."""

from app.modules.visual_search.contracts import EmbeddingDescriptor


SIGLIP_BASELINE_MODEL = "google/siglip-base-patch16-224"
SIGLIP_BASELINE_REVISION = "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"
SIGLIP_BASELINE_DIMENSION = 768
SIGLIP_BASELINE_PREPROCESS_VERSION = "siglip-224-transformers-4.46.3-v1"
VISUAL_SEARCH_BASELINE_SCHEMA_VERSION = "visual_embedding_v1"

VISUAL_SEARCH_BASELINE_DESCRIPTOR = EmbeddingDescriptor(
    encoder_name=SIGLIP_BASELINE_MODEL,
    encoder_revision=SIGLIP_BASELINE_REVISION,
    embedding_schema_version=VISUAL_SEARCH_BASELINE_SCHEMA_VERSION,
    dimension=SIGLIP_BASELINE_DIMENSION,
    preprocess_version=SIGLIP_BASELINE_PREPROCESS_VERSION,
    similarity="cosine",
)
