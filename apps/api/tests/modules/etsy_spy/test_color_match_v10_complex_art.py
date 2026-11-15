"""V10 regression: multicolor real-world layout motifs with role-local contrast.

Synthetic fixtures approximate layout, not reproduction of user artwork.
Transparent originals are immutable. Test foreground roles, NOT arbitrary count.
"""
from __future__ import annotations

from collections import Counter
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.design_artifacts import _validated_color_match

_SHA = "c" * 64


def _artwork(layout: str) -> bytes:
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    headline = ImageFont.load_default(size=15)
    small = ImageFont.load_default(size=9)
    if layout == "veterinary_alphabet":
        # High-volume, ink-dark type with incidental light animal highlights.
        draw.text((12, 4), "VETERINARY ALPHABET", font=headline, fill=(3, 3, 3, 255))
        palette = ("#f6dbab", "#f1f1e6", "#e58e38",
                   "#407c93", "#f6c458", "#969597")
        for i in range(18):
            x, y = 5 + i % 6 * 31, 25 + i // 6 * 53
            draw.text((x + 6, y), chr(65 + i) + chr(97 + i),
                      font=small, fill=(10, 10, 10, 255))
            draw.text((x + 2, y + 11), "VET CARE", font=small,
                      fill=(23, 23, 23, 220))
            draw.ellipse((x + 4, y + 25, x + 23, y + 43),
                         fill=palette[i % len(palette)],
                         outline="#574a35", width=1)
    elif layout == "ducks_in_a_row":
        # Contrast conflict is in yellow/teal lettering; small brown duck
        # outlines are illustration, NOT another mandatory text color.
        for j, line in enumerate(("My ducks are", "absolutely not", "in a row,")):
            draw.text((15, j * 19 + 6), line,
                      font=headline, fill=(245, 192, 99, 255))
        for j, line in enumerate(("I dont even", "know where some", "of them are")):
            draw.text((19, j * 18 + 64), line,
                      font=headline, fill=(59, 145, 123, 255))
        for i in range(5):
            x, y = 9 + (i % 3) * 62, 133 + (i // 3) * 33
            draw.ellipse((x + 6, y, x + 43, y + 26),
                         fill="#f7b62f" if i < 4 else "#947b6d",
                         outline="#50402f", width=2)
    else:
        raise ValueError(layout)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_dark_lettering_with_incidental_light_animal_fill_keeps_light_garments():
    artifact = _artwork("veterinary_alphabet")
    result = recommend_garment_colors(artifact, _SHA)
    assert result is not None
    assert "white" in result["recommended"][:3]
    assert len(result["eligible_colors"]) >= 3
    assert result["analysis"]["rank_policy"] == "text-roi-soft-art-v3"
    assert not set(result["recommended"]) & set(result["unsafe_colors"])
    assert _validated_color_match({"color_match": result}, _SHA)


def test_yellow_and_teal_headline_on_ducks_recovers_dark_neutrals():
    artifact = _artwork("ducks_in_a_row")
    result = recommend_garment_colors(artifact, _SHA)
    assert result is not None
    assert len(result["recommended"]) >= 5
    assert {"true-navy", "graphite", "black"} & set(result["recommended"])
    assert "white" not in result["recommended"]
    assert result["role_counts"]["text"] >= 2
    assert _validated_color_match({"color_match": result}, _SHA)


def test_single_ink_on_same_color_still_rejected():
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((12, 34), "BLACK PRINT", font=ImageFont.load_default(size=20),
              fill=(5, 5, 5, 255))
    buf = BytesIO()
    image.save(buf, "PNG")
    result = recommend_garment_colors(buf.getvalue(), _SHA)
    assert result and "black" not in result["recommended"]
