"""G3 schema contract tests; no database, provider or AI calls."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "app/modules/realistic_review_ugc/typography_dna_contract.py"
spec = importlib.util.spec_from_file_location("typography_dna_contract", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HASH = "a" * 64


def sample():
    return {
        "schema_version": "typography-dna/v1", "analyzer_version": "pilot-1",
        "image_hash": HASH, "typography_region": {"x": .1, "y": .2, "w": .7, "h": .5},
        "style_family": "arched serif", "font_mood": {
            "primary": "serif", "secondary": None, "weight": "heavy",
            "contrast": "medium", "proportions": "wide",
        },
        "layout_graph": {
            "nodes": [
                {"id": "a", "role": "primary", "size_ratio": 1.0, "path_type": "arch", "align": "center"},
                {"id": "b", "role": "secondary", "size_ratio": .5, "path_type": "straight", "align": "center"},
            ],
            "edges": [{"from": "a", "to": "b", "relation": "above", "strength": .8}],
        },
        "overlap_rules": [
            {"a": "a", "b": "b", "region": "center", "reading_priority": "a", "max_occlusion_ratio": .2},
        ],
        "confidence": .82,
    }


def test_valid_normalized_dna():
    result = module.validate_typography_dna(sample(), source_sha256=HASH)
    assert result["image_hash"] == HASH
    assert result["layout_graph"]["nodes"][0]["path_type"] == "arch"
    assert result["confidence"] == .82


@pytest.mark.parametrize("change", [
    lambda x: x.update(image_hash="b" * 64),
    lambda x: x.update(schema_version="v2"),
    lambda x: x["typography_region"].update(x=.8),
    lambda x: x["typography_region"].update(w=0),
    lambda x: x["typography_region"].update(x=float("nan")),
    lambda x: x["layout_graph"]["nodes"].append(dict(x["layout_graph"]["nodes"][0])),
    lambda x: x["layout_graph"]["edges"].append({"from": "a", "to": "z", "relation": "overlap", "strength": .5}),
    lambda x: x["overlap_rules"][0].update(max_occlusion_ratio=1.2),
    lambda x: x.update(confidence=True),
    lambda x: x["font_mood"].update(primary=""),
])
def test_rejects_invalid_or_untrusted_dna(change):
    value = sample()
    change(value)
    with pytest.raises(module.TypographyDnaContractError):
        module.validate_typography_dna(value, source_sha256=HASH)


def test_rejects_wrong_source_hash():
    with pytest.raises(module.TypographyDnaContractError):
        module.validate_typography_dna(sample(), source_sha256="not-a-hash")
