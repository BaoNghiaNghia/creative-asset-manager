from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import main
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR
from siglip import SiglipEncoderLoadError, SiglipVisualEncoder


def test_bootstrap_uses_local_siglip_runtime_and_matches_api_descriptor() -> None:
    assert main.SiglipVisualEncoder is SiglipVisualEncoder
    assert SiglipVisualEncoder.__module__ == "siglip"
    assert SiglipVisualEncoder.descriptor == VISUAL_SEARCH_BASELINE_DESCRIPTOR


def test_siglip_rejects_any_non_pinned_snapshot_path(tmp_path: Path) -> None:
    with pytest.raises(SiglipEncoderLoadError):
        SiglipVisualEncoder(tmp_path)
