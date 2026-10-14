"""Regression: Stage 1 uses bundled DaFont glyph files, not guessed typefaces."""
from pathlib import Path

from PIL import Image

from app.modules.realistic_review_ugc.stage1_font_library import (
    FONT_LIBRARY, font_brief, font_info_for_number, render_font_guides,
)


def test_approved_fonts_and_licenses_are_bundled():
    assert len(FONT_LIBRARY) == 6
    assert {row["font"] for row in FONT_LIBRARY} == {"Bebas Neue", "Pacifico", "Lobster"}
    for number in range(1, 7):
        report = font_info_for_number(number)
        assert report["font_source"] == "DaFont"
        assert report["font_status"] == "layout_reference"
        assert report["font_license"] == "SIL OFL 1.1"
        assert Path(__file__).resolve().parents[5].joinpath(
            "deploy/codex/skills/gatorhats-stage1-six-designs/fonts", report["font_file"]
        ).is_file()
    assert "design_06.png" in font_brief()


def test_real_font_guides_have_transparency_and_brown_thread(tmp_path):
    folder = render_font_guides(tmp_path, "RESPECT YOUR MOTHER")
    guides = sorted(folder.glob("design_*.png"))
    assert len(guides) == 6
    for path in guides:
        with Image.open(path) as im:
            im.load()
            assert im.mode == "RGBA"
            assert im.size == (1600, 1150)
            assert im.getpixel((0, 0))[3] == 0
            center = im.getchannel("A")
            assert center.getbbox() is not None
            pixels = im.convert("RGBA").getdata()
            colors = {pixel[:3] for pixel in pixels if pixel[3] >= 254}
            assert colors <= {(107, 63, 40), (116, 71, 48)}
