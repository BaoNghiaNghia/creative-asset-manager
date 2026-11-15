"""ColorMatch V7: faded type, spatial components, medium-resolution analysis.

All test images are original synthetic alpha PNG fixtures. No production
artwork, OCR, Drive access or external services.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.color_match_palette import ALGORITHM, GARMENT_COLORS
from app.modules.etsy_spy.design_artifacts import _validated_color_match
from app.modules.etsy_spy.color_match_readability import analyze_ink_regions


def make_png(alpha: int = 255, *, edge: int = 192, tiny: bool = False) -> bytes:
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((edge // 5, edge // 3, 4 * edge // 5, 5 * edge // 6),
              fill="#E5389C")
    font = ImageFont.load_default(size=max(8, int(edge * (.06 if tiny else .09))))
    for n in range(3):
        d.text((edge // 20, edge // 40 + n * edge // 9),
               "VINTAGE FADED BLACK", font=font, fill=(8, 8, 8, alpha))
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("alpha", [85, 115, 145, 154, 155, 200, 255])
def test_faded_typography_always_rejects_dark_and_preserves_white(alpha: int):
    result = recommend_garment_colors(make_png(alpha), "a" * 64)
    assert result and result["version"] == ALGORITHM
    assert "true-navy" in result["unsafe_colors"]
    assert "black" in result["unsafe_colors"]
    assert "white" in result["recommended"]
    assert 0 <= len(result["recommended"]) <= 10
    assert not set(result["recommended"]).intersection(result["unsafe_colors"])
    assert set(result["recommended"]).issubset(result["eligible_colors"])
    assert _validated_color_match({"color_match": result}, "a" * 64)


def test_medium_analysis_captures_real_small_text_regions():
    original = Image.open(BytesIO(make_png(170, edge=512, tiny=True)))
    thumb = original.copy()
    thumb.thumbnail((192, 192), Image.Resampling.LANCZOS)
    small = BytesIO()
    thumb.save(small, "PNG")
    result = recommend_garment_colors(
        small.getvalue(), "b" * 64, analysis_png=make_png(170, edge=512, tiny=True))
    assert result and result["analysis"]["source"] == "medium"
    assert result["analysis"]["resolution"] == [512, 512]
    assert result["analysis"]["text_regions"] >= 1
    assert "true-navy" in result["unsafe_colors"]
    assert _validated_color_match({"color_match": result}, "b" * 64)


def test_invalid_medium_analysis_falls_back_to_verified_thumbnail():
    result = recommend_garment_colors(make_png(), "c" * 64,
                                      analysis_png=b"not a valid medium preview")
    assert result and result["analysis"]["source"] == "thumbnail"
    assert result["analysis"]["print_review_required"] is True


def test_only_colored_illustration_does_not_become_fake_black_lettering():
    image = Image.new("RGBA", (192, 192))
    d = ImageDraw.Draw(image)
    d.ellipse((8, 8, 185, 185), fill="#F92E80")
    d.ellipse((45, 45, 146, 145), fill="#13B9AB")
    buf = BytesIO()
    image.save(buf, "PNG")
    result = recommend_garment_colors(buf.getvalue(), "d" * 64)
    assert result
    assert result["eligible_colors"]
    assert len(result["unsafe_colors"]) < len(GARMENT_COLORS)


def test_v7_metadata_rejects_invented_eligible_or_spoofed_regions():
    result = recommend_garment_colors(make_png(), "e" * 64)
    assert result and _validated_color_match({"color_match": result}, "e" * 64)
    assert not _validated_color_match({"color_match": {
        **result, "eligible_colors": ["fake-color"],
    }}, "e" * 64)
    assert not _validated_color_match({"color_match": {
        **result, "eligible_colors": result["eligible_colors"][:1],
    }}, "e" * 64)
    assert not _validated_color_match({"color_match": {
        **result, "analysis": {**result["analysis"], "regions": [
            {"kind": "probable_text", "pigment": "dark-ink",
             "bbox": [-1, 0, 100, 200], "opacity": .8},
        ]},
    }}, "e" * 64)


def test_no_readable_color_remains_explicit_and_sha_bound():
    image = Image.new("RGBA", (192, 192))
    d = ImageDraw.Draw(image)
    d.text((2, 3), "BLACK INK", fill="#020202", font=ImageFont.load_default(size=19))
    d.text((2, 135), "WHITE INK", fill="#FAFAFA", font=ImageFont.load_default(size=19))
    buf = BytesIO()
    image.save(buf, "PNG")
    result = recommend_garment_colors(buf.getvalue(), "f" * 64)
    assert result
    assert result["insufficient_safe_colors"] == (len(result["recommended"]) < 7)
    assert _validated_color_match({"color_match": result}, "f" * 64)
    assert not _validated_color_match({"color_match": result}, "e" * 64)
