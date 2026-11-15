"""V9 only recommends quality-gated colors; all others remain manual previews."""
from __future__ import annotations

from io import BytesIO
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.color_match_palette import ALGORITHM, GARMENT_COLORS
from app.modules.etsy_spy.design_artifacts import _validated_color_match

SHA = "a" * 64


def artwork(case: str, alpha: int = 255) -> bytes:
    image = Image.new("RGBA", (192, 192))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=20)
    if case == "dark":
        draw.text((8, 9), "BLACK FONT", fill=(6, 6, 6, alpha), font=font)
        draw.ellipse((25, 66, 156, 170), fill="#FF4299")
    elif case == "light":
        draw.text((8, 9), "WHITE FONT", fill="#FDFDFD", font=font)
        draw.ellipse((25, 66, 156, 170), fill="#00B9AB")
    elif case == "mixed":
        draw.text((8, 9), "BLACK FONT", fill="#060606", font=font)
        draw.text((8, 136), "WHITE FONT", fill="#FFFFFF", font=font)
    elif case == "color":
        draw.text((8, 9), "PINK FONT", fill="#ED2476", font=font)
        draw.ellipse((25, 66, 156, 170), fill="#17AA8D")
    elif case == "red":
        draw.text((8, 9), "RED TEXT", fill="#C8102E", font=font)
    else:
        raise AssertionError("unknown fixture")
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("style", ("dark", "light", "mixed", "color", "red"))
def test_rank_only_quality_colors_with_sha_guard(style):
    meta = recommend_garment_colors(artwork(style), SHA)
    assert meta is not None and meta["version"] == ALGORITHM
    assert ALGORITHM == "colormatch-sms-c1717-v9"
    top = meta["recommended"]
    assert 0 <= len(top) <= 10 and len(top) == len(set(top))
    assert set(top) <= {c for c, _ in GARMENT_COLORS}
    assert set(top) <= set(meta["eligible_colors"])
    assert set(top).isdisjoint(meta["unsafe_colors"])
    assert meta["review_colors"] == []
    assert meta["insufficient_safe_colors"] == (len(meta["eligible_colors"]) < 7)
    assert top == meta["eligible_colors"][:len(top)]
    assert _validated_color_match({"color_match": meta}, SHA)
    assert _validated_color_match({"color_match": meta}, "b" * 64) is None
    assert recommend_garment_colors(artwork(style), SHA)["recommended"] == top


def test_all_bad_colors_stay_in_remaining_palette_instead_of_forced_top_ten():
    meta = recommend_garment_colors(artwork("mixed"), SHA)
    assert meta and meta["recommended"] == [] and meta["eligible_colors"] == []
    assert meta["insufficient_safe_colors"] is True
    assert _validated_color_match({"color_match": meta}, SHA)


@pytest.mark.parametrize("alpha", (85, 115, 145, 154, 155, 255))
def test_faded_dark_text_protects_top_choices(alpha):
    meta = recommend_garment_colors(artwork("dark", alpha=alpha), SHA)
    assert meta and 0 <= len(meta["recommended"]) <= 10
    assert "true-navy" in meta["unsafe_colors"] and "black" in meta["unsafe_colors"]
    assert "black" not in meta["recommended"]
    if alpha == 255:
        assert "white" in meta["recommended"]


def test_pigment_matching_red_garment_not_recommended_for_red_typography():
    meta = recommend_garment_colors(artwork("red"), SHA)
    assert meta and meta["recommended"]
    assert "red" not in meta["recommended"]
    assert "crimson" not in meta["recommended"]
    assert meta["recommended"][0] in {"white", "banana", "butter", "ivory", "chambray"}


def test_forged_payload_is_rejected():
    meta = recommend_garment_colors(artwork("dark"), SHA)
    assert meta and _validated_color_match({"color_match": meta}, SHA)
    for bad in (
        {**meta, "recommended": ["black"]},
        {**meta, "recommended": meta["recommended"] + meta["recommended"][:1]},
        {**meta, "review_colors": ["red"]},
        {**meta, "unsafe_colors": ["nonexistent"]},
        {**meta, "insufficient_safe_colors": not meta["insufficient_safe_colors"]},
        {**meta, "readability_check": "region_ink_alpha_composite_v2"},
    ):
        assert _validated_color_match({"color_match": bad}, SHA) is None


def test_legacy_v8_and_v7_records_remain_readable_until_sha_bound_backfill():
    meta = recommend_garment_colors(artwork("dark"), SHA)
    assert meta
    old_top = [c for c, _hex in GARMENT_COLORS][:10]
    v8 = {**meta, "version": "colormatch-sms-c1717-v8",
          "method": "design_region_alpha_sales_prior_ten",
          "readability_check": "region_ink_alpha_composite_v2",
          "recommended": old_top,
          "review_colors": [c for c in old_top if c not in meta["eligible_colors"]]}
    assert _validated_color_match({"color_match": v8}, SHA)
    v7 = {**meta, "version": "colormatch-sms-c1717-v7",
          "method": "design_region_alpha_sales_prior",
          "readability_check": "region_ink_alpha_composite_v2",
          "recommended": meta["recommended"], "review_colors": []}
    assert _validated_color_match({"color_match": v7}, SHA)


def test_tiny_chest_mark_can_return_fewer_than_ten_no_artwork_is_skipped():
    image = Image.new("RGBA", (192, 192))
    ImageDraw.Draw(image).rectangle((90, 90, 93, 93), fill="#121212")
    buf = BytesIO()
    image.save(buf, "PNG")
    meta = recommend_garment_colors(buf.getvalue(), SHA)
    assert meta and 1 <= len(meta["recommended"]) <= 10
    assert _validated_color_match({"color_match": meta}, SHA)


def test_fully_transparent_artwork_never_receives_invented_colors():
    image = Image.new("RGBA", (192, 192))
    buf = BytesIO()
    image.save(buf, "PNG")
    assert recommend_garment_colors(buf.getvalue(), SHA) is None
