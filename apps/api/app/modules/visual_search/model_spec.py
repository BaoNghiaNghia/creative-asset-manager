"""Versioned Visual Search compatibility metadata with no ML runtime dependency."""

from app.modules.visual_search.contracts import EmbeddingDescriptor


# Previous production embedding contract. Keep this descriptor available for
# rollback/inspection while visual_embedding_v2 is built and validated.
SIGLIP_V1_MODEL = "google/siglip-base-patch16-224"
SIGLIP_V1_REVISION = "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"
SIGLIP_V1_DIMENSION = 768
SIGLIP_V1_PREPROCESS_VERSION = "siglip-224-transformers-4.46.3-v1"
VISUAL_SEARCH_V1_SCHEMA_VERSION = "visual_embedding_v1"

VISUAL_SEARCH_V1_DESCRIPTOR = EmbeddingDescriptor(
    encoder_name=SIGLIP_V1_MODEL,
    encoder_revision=SIGLIP_V1_REVISION,
    embedding_schema_version=VISUAL_SEARCH_V1_SCHEMA_VERSION,
    dimension=SIGLIP_V1_DIMENSION,
    preprocess_version=SIGLIP_V1_PREPROCESS_VERSION,
    similarity="cosine",
)


# VS-CPU-01 target. The revision is an immutable Hugging Face snapshot and the
# dimension/preprocess contract is intentionally explicit so v1/v2 vectors can
# never be mixed by accident.
SIGLIP2_MODEL = "google/siglip2-base-patch16-224"
SIGLIP2_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
SIGLIP2_DIMENSION = 768
SIGLIP2_PREPROCESS_VERSION = "siglip2-224-transformers-4.51.3-v1"
VISUAL_SEARCH_V2_SCHEMA_VERSION = "visual_embedding_v2"

VISUAL_SEARCH_V2_DESCRIPTOR = EmbeddingDescriptor(
    encoder_name=SIGLIP2_MODEL,
    encoder_revision=SIGLIP2_REVISION,
    embedding_schema_version=VISUAL_SEARCH_V2_SCHEMA_VERSION,
    dimension=SIGLIP2_DIMENSION,
    preprocess_version=SIGLIP2_PREPROCESS_VERSION,
    similarity="cosine",
)


# New work must use the active descriptor. The dedicated Elasticsearch index
# prefix includes embedding_schema_version, therefore activating v2 creates a
# distinct v2 index/alias namespace and leaves v1 available for rollback.
VISUAL_SEARCH_ACTIVE_DESCRIPTOR = VISUAL_SEARCH_V2_DESCRIPTOR

# Compatibility aliases for callers that have not yet been renamed. They point
# at the active contract; explicit rollback code must use VISUAL_SEARCH_V1_DESCRIPTOR.
SIGLIP_BASELINE_MODEL = SIGLIP2_MODEL
SIGLIP_BASELINE_REVISION = SIGLIP2_REVISION
SIGLIP_BASELINE_DIMENSION = SIGLIP2_DIMENSION
SIGLIP_BASELINE_PREPROCESS_VERSION = SIGLIP2_PREPROCESS_VERSION
VISUAL_SEARCH_BASELINE_SCHEMA_VERSION = VISUAL_SEARCH_V2_SCHEMA_VERSION
VISUAL_SEARCH_BASELINE_DESCRIPTOR = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
