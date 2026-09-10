from __future__ import annotations

from app.modules.visual_search.model_spec import (
    SIGLIP_BASELINE_DIMENSION,
    SIGLIP_BASELINE_MODEL,
    SIGLIP_BASELINE_REVISION,
    VISUAL_SEARCH_BASELINE_DESCRIPTOR,
)


def test_siglip_baseline_descriptor_is_pinned_and_lightweight() -> None:
    assert VISUAL_SEARCH_BASELINE_DESCRIPTOR.encoder_name == SIGLIP_BASELINE_MODEL
    assert VISUAL_SEARCH_BASELINE_DESCRIPTOR.encoder_revision == SIGLIP_BASELINE_REVISION
    assert VISUAL_SEARCH_BASELINE_DESCRIPTOR.dimension == SIGLIP_BASELINE_DIMENSION
