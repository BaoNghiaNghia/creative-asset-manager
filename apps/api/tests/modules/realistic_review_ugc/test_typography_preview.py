"""G4 preview-only composer: exact text, original structure, no provider calls."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.modules.realistic_review_ugc.typography_preview import (
    PREVIEW_VERSION, TypographyPreviewError, _groups, build_typography_previews,
)


def dna():
    return {
        "schema_version": "typography-dna/v1",
        "analyzer_version": "g3-layout-font-overlap-v1",
        "image_hash": "b" * 64,
        "typography_region": {"x": .13, "y": .18, "w": .67, "h": .54},
        "style_family": "arched-serif",
        "font_mood": {
            "primary": "serif", "secondary": None,
            "weight": "heavy", "contrast": "medium", "proportions": "wide",
        },
        "layout_graph": {
            "nodes": [
                {"id": "n1", "role": "primary", "path_type": "arch",
                 "align": "center", "size_ratio": 1.0},
            ],
            "edges": [],
        },
        "overlap_rules": [],
        "confidence": .92,
    }


def render(keyword, count=3):
    return build_typography_previews(
        keyword, dna=dna(), reference_id="ref-a",
        dna_id="dna-a", count=count,
    )


def svg_rendered_text(svg):
    root = ET.fromstring(svg)
    return "".join(
        "".join(node.itertext())
        for node in root.iter() if node.tag.endswith("}text")
    )


@pytest.mark.parametrize("keyword", [
    "BEACH BABE", "Cà phê Việt Nam 2026!",
    "I ♥ Nha Trang", "Coffee & Co <3",
    "SÀI GÒN   MÙA THU", "Cảm ơn bạn!",
    "Singleword", "  A B  ",
    "Curved typography meets culture and travel photography in Việt Nam.",
])
def test_svg_exact_keyword_utf8_xss_escaping_and_determinism(keyword):
    previews = render(keyword)
    assert len(previews) == 3
    assert len({item["id"] for item in previews}) == 3
    assert len({item["svg"] for item in previews}) == 3
    assert previews == render(keyword)
    for item in previews:
        assert item["source_keyword_exact"] == keyword
        assert svg_rendered_text(item["svg"]) == keyword
        assert item["preview_version"] == PREVIEW_VERSION
        assert item["transparent_background"] is True
        assert item["preview_only"] is True
        assert item["eligible_for_generation"] is False
        assert item["qa"]["exact_keyword"] is True
        assert item["qa"]["requires_human_review"] is True
        assert "<script>" not in item["svg"]
        assert "<foreignObject" not in item["svg"]
        assert 'http://' not in item["svg"].replace('http://www.w3.org/2000/svg', '')
        assert 'url(' not in item["svg"]
        assert "<rect" not in item["svg"]


def test_line_wrap_preserves_all_original_spaces():
    for count in (1, 2, 3):
        parts = _groups("Big  ideas for everyone", count)
        assert len(parts) <= count
        assert "".join(parts) == "Big  ideas for everyone"


@pytest.mark.parametrize("keyword", [
    "", " ", "a" * 161, "A\nB", "A\tB",
    "Text\u202emirror", "Text\u200eforce", "\u200bHidden",
    " ".join(["a"] * 31),
])
def test_invisible_control_blank_and_oversized_keywords_fail_closed(keyword):
    with pytest.raises(TypographyPreviewError):
        render(keyword)


def test_bounds_and_untrusted_dna_fail_closed():
    with pytest.raises(TypographyPreviewError):
        render("Normal", count=4)
    with pytest.raises(TypographyPreviewError):
        render("Normal", count=True)
    tampered = dna()
    tampered["image_hash"] = "invalid"
    with pytest.raises(TypographyPreviewError):
        build_typography_previews(
            "Normal", dna=tampered, reference_id="ref-a", dna_id="dna-a",
        )
    with pytest.raises(TypographyPreviewError):
        build_typography_previews(
            "Normal", dna=dna(), reference_id="", dna_id="dna-a",
        )


def test_many_words_remain_readable_and_bounded():
    keyword = " ".join(f"VietNam{i}" for i in range(14))
    preview = render(keyword)
    assert all(len(item["svg"]) < 9000 for item in preview)
    assert all(svg_rendered_text(item["svg"]) == keyword for item in preview)
