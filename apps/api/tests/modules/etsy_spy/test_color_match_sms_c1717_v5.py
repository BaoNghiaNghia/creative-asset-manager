"""SMS C1717 palette snapshot/algorithm regression (never implies live inventory)."""
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageDraw

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.color_match_palette import (
    ALGORITHM, GARMENT_COLORS, SMS_C1717_COLORS, LEGACY_COLOR_IDS,
)
from app.modules.etsy_spy.design_artifacts import _validated_color_match


def test_sms_c1717_catalog_is_complete_and_status_is_not_stock():
    assert ALGORITHM == "colormatch-sms-c1717-v5"
    assert len(SMS_C1717_COLORS) == 72
    assert len({row[0] for row in SMS_C1717_COLORS}) == 72
    assert sum(row[3] for row in SMS_C1717_COLORS) == 56
    assert sum(not row[3] for row in SMS_C1717_COLORS) == 16
    assert len(GARMENT_COLORS) == 54
    assert {row[1] for row in SMS_C1717_COLORS if row[2] is None} == {
        "Grass", "Topaz Blue", "Mystic"
    }
    colors = dict(GARMENT_COLORS)
    assert colors["blue-spruce"] == "#004C45"
    assert colors["true-navy"] == "#041E42"
    assert colors["espresso"] == "#533625"
    assert colors["sandstone"] == "#A69F88"
    assert colors["terracotta"] == "#E3775E"
    assert "natural" in LEGACY_COLOR_IDS and "natural" not in colors
    assert not {"grass", "topaz-blue", "mystic"}.intersection(colors)


def test_v5_only_recommends_real_sms_configured_hex_colors():
    image = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 20, 169, 159), fill="#F6EFE4")
    preview = BytesIO()
    image.save(preview, format="PNG")
    sha = "a" * 64
    recommendation = recommend_garment_colors(preview.getvalue(), sha)
    assert recommendation and recommendation["version"] == ALGORITHM
    assert 7 <= len(recommendation["recommended"]) <= 10
    assert set(recommendation["recommended"]) <= {name for name, _ in GARMENT_COLORS}
    assert _validated_color_match({"color_match": recommendation}, sha)
    assert not _validated_color_match({"color_match": recommendation}, "b" * 64)
    assert not _validated_color_match({"color_match": {
        **recommendation, "recommended": ["grass"],
    }}, sha)
    legacy = {**recommendation, "version": "colormatch-pixel-v3",
              "recommended": ["natural", "terracota"]}
    assert _validated_color_match({"color_match": legacy}, sha)


def test_frontend_palette_matches_backend_c1717_sms_snapshot():
    path = Path(__file__).resolve().parents[5] / "apps/client/app/etsy-spy/c1717SmsColors.ts"
    text = path.read_text(encoding="utf-8")
    entries = re.findall(
        r'\{ id: "([^"]+)", label: "([^"]+)", color: (null|"#[0-9A-F]{6}"), setUp: (true|false) \}',
        text,
    )
    assert len(entries) == 72
    expected = [
        (key, name, "null" if color is None else f'"{color}"',
         "true" if configured else "false")
        for key, name, color, configured in SMS_C1717_COLORS
    ]
    assert entries == expected
