"""Regression guards for dark typography disappearing on C1717 Black/Navy.

The image fixtures are synthetic transparent PNGs resembling the reported
flamingo-typography and veterinary alphabet layouts, not extracted user art.
No OCR, regeneration, cloud fetch or production file writes are involved.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.color_match_palette import ALGORITHM, GARMENT_COLORS
from app.modules.etsy_spy.design_artifacts import _validated_color_match


def _thumbnail(style: str) -> bytes:
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = "#080A0C"
    if style == "flamingo":
        big = ImageFont.load_default(size=16)
        draw.text((4, 4), "YOU DONT HAVE TO BE", font=big, fill=ink)
        draw.text((5, 24), "TO QUILT WITH US", font=big, fill=ink)
        draw.ellipse((35, 45, 152, 144), fill="#E32D91")
        draw.ellipse((60, 65, 127, 137), fill="#F1A934")
        draw.text((9, 163), "WE CAN TRAIN YOU", font=big, fill=ink)
    elif style == "veterinary":
        header = ImageFont.load_default(size=16)
        small = ImageFont.load_default(size=10)
        draw.text((3, 2), "VETERINARY ALPHABET", font=header, fill=ink)
        for idx in range(8):
            y = 27 + idx * 19
            draw.text((6, y), "Aa Bb Cc Dd Ee", font=small, fill=ink)
            for j, color in enumerate(("#E7A049", "#EDC978", "#80A7C3", "#F9E9C8")):
                draw.ellipse((7 + 38 * j, y + 8, 20 + 38 * j, y + 15), fill=color)
    elif style == "white-and-black-ink":
        draw.rectangle((18, 15, 175, 88), fill="white")
        draw.rectangle((18, 95, 175, 175), fill="#070707")
    elif style == "black-text":
        draw.text((6, 40), "BLACK TYPE ON SHIRT", font=ImageFont.load_default(size=19), fill=ink)
    else:
        raise ValueError(style)
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_flamingo_black_headline_never_gets_navy_pepper_or_black():
    assert ALGORITHM == "colormatch-sms-c1717-v9"
    result = recommend_garment_colors(_thumbnail("flamingo"), "a" * 64)
    assert result and result["version"] == ALGORITHM
    for invisible in ("true-navy", "black", "pepper"):
        assert invisible not in result["recommended"]
        assert invisible in result["unsafe_colors"]
    assert "white" in result["recommended"]
    assert 0 <= len(result["recommended"]) <= 10
    assert result["method"] == "design_region_alpha_quality_first"
    assert result["readability_check"] == "region_ink_alpha_composite_v3"
    assert _validated_color_match({"color_match": result}, "a" * 64)


def test_veterinary_small_black_letters_are_not_outvoted_by_bright_animals():
    result = recommend_garment_colors(_thumbnail("veterinary"), "b" * 64)
    assert result
    for invisible in ("true-navy", "black", "pepper"):
        assert invisible in result["unsafe_colors"]
        assert invisible not in result["recommended"]
    assert 0 <= len(result["recommended"]) <= 10
    assert result["insufficient_safe_colors"] is (len(result["eligible_colors"]) < 7)
    assert _validated_color_match({"color_match": result}, "b" * 64)


def test_zero_safe_colors_stores_unsafe_metadata_not_a_fake_recommendation():
    result = recommend_garment_colors(_thumbnail("white-and-black-ink"), "c" * 64)
    assert result
    assert result["insufficient_safe_colors"] is (len(result["eligible_colors"]) < 7)
    assert 0 <= len(result["recommended"]) <= 10
    assert set(result["recommended"]).isdisjoint(result["unsafe_colors"])
    assert result["review_colors"] == []
    assert _validated_color_match({"color_match": result}, "c" * 64)
    assert _validated_color_match({"color_match": result}, "d" * 64) is None


def test_v6_metadata_rejects_unsound_unsafe_color_advice():
    result = recommend_garment_colors(_thumbnail("black-text"), "d" * 64)
    assert result and _validated_color_match({"color_match": result}, "d" * 64)
    assert len(result["unsafe_colors"]) <= len(GARMENT_COLORS)
    assert _validated_color_match({"color_match": {**result, "unsafe_colors": ["fake"]}}, "d" * 64) is None
    assert _validated_color_match({"color_match": {
        **result, "unsafe_colors": ["black", "black"]
    }}, "d" * 64) is None
    assert _validated_color_match({"color_match": {
        **result, "readability_check": "unknown"
    }}, "d" * 64) is None
    if result["recommended"]:
        assert _validated_color_match({"color_match": {
            **result, "unsafe_colors": [result["recommended"][0]]
        }}, "d" * 64) is None
