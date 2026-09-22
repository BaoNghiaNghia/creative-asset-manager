from __future__ import annotations

from app.modules.visual_search.model_spec import (
    SIGLIP2_DIMENSION,
    SIGLIP2_MODEL,
    SIGLIP2_PREPROCESS_VERSION,
    SIGLIP2_REVISION,
    SIGLIP_V1_MODEL,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
    VISUAL_SEARCH_V1_DESCRIPTOR,
    VISUAL_SEARCH_V2_DESCRIPTOR,
)


def test_siglip2_active_descriptor_is_pinned_and_versioned() -> None:
    assert VISUAL_SEARCH_ACTIVE_DESCRIPTOR is VISUAL_SEARCH_V2_DESCRIPTOR
    assert VISUAL_SEARCH_V2_DESCRIPTOR.encoder_name == SIGLIP2_MODEL
    assert VISUAL_SEARCH_V2_DESCRIPTOR.encoder_revision == SIGLIP2_REVISION
    assert VISUAL_SEARCH_V2_DESCRIPTOR.dimension == SIGLIP2_DIMENSION
    assert VISUAL_SEARCH_V2_DESCRIPTOR.preprocess_version == SIGLIP2_PREPROCESS_VERSION
    assert VISUAL_SEARCH_V2_DESCRIPTOR.embedding_schema_version == "visual_embedding_v2"


def test_siglip_v1_descriptor_remains_distinct_for_rollback() -> None:
    assert VISUAL_SEARCH_V1_DESCRIPTOR.encoder_name == SIGLIP_V1_MODEL
    assert VISUAL_SEARCH_V1_DESCRIPTOR.embedding_schema_version == "visual_embedding_v1"
    assert VISUAL_SEARCH_V1_DESCRIPTOR != VISUAL_SEARCH_V2_DESCRIPTOR
