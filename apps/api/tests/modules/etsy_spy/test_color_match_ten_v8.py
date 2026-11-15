"""V8 exactly ten automatic garment previews, without claiming unsafe ink is printable."""
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
    else:
        raise AssertionError("unknown fixture")
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("style", ("dark", "light", "mixed", "color"))
def test_each_valid_artwork_receives_ten_distinct_sha_bound_colors(style):
    meta = recommend_garment_colors(artwork(style), SHA)
    assert meta is not None and meta["version"] == ALGORITHM
    assert ALGORITHM == "colormatch-sms-c1717-v8"
    assert len(meta["recommended"]) == len(set(meta["recommended"])) == 10
    assert set(meta["recommended"]) <= {c for c, _ in GARMENT_COLORS}
    assert set(meta["review_colors"]) == set(meta["recommended"]) - set(meta["eligible_colors"])
    assert (set(meta["recommended"]) & set(meta["unsafe_colors"])).issubset(meta["review_colors"])
    assert meta["insufficient_safe_colors"] == (len(meta["eligible_colors"]) < 7)
    assert _validated_color_match({"color_match": meta}, SHA)
    assert _validated_color_match({"color_match": meta}, "b" * 64) is None
    assert recommend_garment_colors(artwork(style), SHA)["recommended"] == meta["recommended"]


def test_when_every_color_fails_hard_readability_ten_options_are_flagged():
    meta = recommend_garment_colors(artwork("mixed"), SHA)
    assert meta is not None and len(meta["recommended"]) == 10
    assert meta["eligible_colors"] == []
    assert set(meta["review_colors"]) == set(meta["recommended"])
    assert set(meta["recommended"]).issubset(meta["unsafe_colors"])
    assert _validated_color_match({"color_match": meta}, SHA)


@pytest.mark.parametrize("alpha", (85, 115, 145, 154, 155, 255))
def test_faded_dark_text_protects_top_choices(alpha):
    meta = recommend_garment_colors(artwork("dark", alpha=alpha), SHA)
    assert meta and len(meta["recommended"]) == 10
    assert "true-navy" in meta["unsafe_colors"] and "black" in meta["unsafe_colors"]
    assert "white" in meta["recommended"]
    assert "black" not in meta["recommended"]


def test_forged_review_or_non_ten_payload_is_not_projected():
    meta = recommend_garment_colors(artwork("dark"), SHA)
    assert meta and _validated_color_match({"color_match": meta}, SHA)
    for bad in (
        {**meta, "recommended": meta["recommended"][:9]},
        {**meta, "review_colors": ["fake-color"]},
        {**meta, "review_colors": [] if meta["review_colors"] else ["black"]},
        {**meta, "unsafe_colors": ["nonexistent"]},
        {**meta, "insufficient_safe_colors": not meta["insufficient_safe_colors"]},
    ):
        assert _validated_color_match({"color_match": bad}, SHA) is None


def test_old_v7_metadata_stays_readable_during_v8_backfill():
    meta = recommend_garment_colors(artwork("dark"), SHA)
    assert meta
    legacy = {
        **meta,
        "version": "colormatch-sms-c1717-v7",
        "method": "design_region_alpha_sales_prior",
        "recommended": [id for id in meta["recommended"] if id in meta["eligible_colors"]][:7],
        "insufficient_safe_colors": False,
    }
    assert len(legacy["recommended"]) == 7
    assert _validated_color_match({"color_match": legacy}, SHA)

def test_tiny_single_chest_mark_gets_ten_instead_of_being_skipped():
    image = Image.new("RGBA", (192, 192))
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 90, 93, 93), fill="#121212")
    buf = BytesIO()
    image.save(buf, "PNG")
    meta = recommend_garment_colors(buf.getvalue(), SHA)
    assert meta and len(meta["recommended"]) == 10
    assert _validated_color_match({"color_match": meta}, SHA)


def test_empty_transparent_artwork_is_not_falsely_assigned_colors():
    image = Image.new("RGBA", (192, 192))
    buf = BytesIO()
    image.save(buf, "PNG")
    assert recommend_garment_colors(buf.getvalue(), SHA) is None
