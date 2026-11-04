from app.modules.realistic_review_ugc.heritage_letterform_dna import derive_letterform_traits
from app.modules.realistic_review_ugc.heritage_lettering import heritage_fit_advisory


def test_g3_structural_heritage_tags_never_contain_source_words():
    reviewed = {
        "font_mood": {"primary": "serif", "secondary": "script"},
        "layout_graph": {
            "nodes": [
                {"id": "n1", "path_type": "arch", "size_ratio": 2.0},
                {"id": "n2", "path_type": "straight", "size_ratio": 1.0},
            ],
            "edges": [{"from": "n1", "to": "n2", "relation": "overlap"}],
        },
    }
    traits = derive_letterform_traits(reviewed)
    assert traits["mixed_families"]
    assert traits["curved_baseline"]
    assert traits["overlap"]
    assert traits["strong_hierarchy"]
    assert "heritage_serif_candidate" in traits["style_tags"]
    assert traits["custom_glyphs_verified"] is False
    assert traits["embroidery_verified"] is False


def test_missing_g3_geometry_returns_conservative_hints():
    traits = derive_letterform_traits({})
    assert traits["style_tags"] == []
    assert traits["primary_family"] == "unknown"
    assert not traits["custom_glyphs_verified"]


def test_heritage_qa_warns_without_false_embroidery_certification():
    good = heritage_fit_advisory([120, 170, 1920, 930])
    assert good["flags"] == []
    assert good["verified_lettering"] is False
    assert good["verified_embroidery"] is False
    assert "heritage_layout_too_wide_review" in heritage_fit_advisory([60, 320, 1980, 600])["flags"]
    assert "bbox_unavailable" in heritage_fit_advisory(None)["flags"]
    assert "bbox_invalid" in heritage_fit_advisory([False, 10, 20, 30])["flags"]
