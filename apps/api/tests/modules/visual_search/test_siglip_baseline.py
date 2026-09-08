from __future__ import annotations

import sys

import pytest

from app.modules.visual_search.encoder import (
    SIGLIP_BASELINE_DIMENSION,
    SIGLIP_BASELINE_MODEL,
    SIGLIP_BASELINE_REVISION,
    SiglipEncoderLoadError,
    SiglipVisualEncoder,
)


def test_siglip_baseline_descriptor_is_pinned_and_import_is_lightweight() -> None:
    assert SiglipVisualEncoder.descriptor.encoder_name == SIGLIP_BASELINE_MODEL
    assert SiglipVisualEncoder.descriptor.encoder_revision == SIGLIP_BASELINE_REVISION
    assert SiglipVisualEncoder.descriptor.dimension == SIGLIP_BASELINE_DIMENSION
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_siglip_rejects_any_non_pinned_snapshot_path(tmp_path) -> None:
    with pytest.raises(SiglipEncoderLoadError):
        SiglipVisualEncoder(tmp_path)
