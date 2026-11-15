"""V10 regression: role-aware visual ranking, conservative review, SHA binding.

All fixtures are synthetic alpha PNGs. No network, no original art edits.
"""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import pytest

from app.modules.etsy_spy import color_match
from app.modules.etsy_spy.color_match_palette import ALGORITHM, GARMENT_COLORS
from app.modules.etsy_spy.color_match_v10 import delta_e_2000
from app.modules.etsy_spy.design_artifacts import _validated_color_match

SHA = "a" * 64
FONT = ImageFont.load_default(size=20)


def fixture(style: str, *, edge: int = 192) -> bytes:
    im = Image.new("RGBA", (edge, edge))
    d = ImageDraw.Draw(im)
    if style == "dark":
        d.text((5, 7), "BLACK FONT", font=FONT, fill="#050505")
        d.ellipse((20, 60, 165, 172), fill="#F8278B")
    elif style == "light":
        d.text((5, 7), "WHITE FONT", font=FONT, fill="#FFFFFF")
        d.ellipse((20, 60, 165, 172), fill="#22BFAA")
    elif style == "mixed":
        d.text((5, 9), "BLACK FONT", font=FONT, fill="#050505")
        d.text((5, 136), "WHITE FONT", font=FONT, fill="#FFFFFF")
    elif style == "mixed-art":
        d.text((5, 9), "BLACK FONT", font=FONT, fill="#050505")
        d.ellipse((25, 68, 155, 174), fill="#F8F8F8")
    elif style == "red":
        d.text((5, 9), "RED TEXT", font=FONT, fill="#C8102E")
    elif style == "colorful":
        for i, rgb in enumerate(("#11BFA7", "#F8B833", "#271551", "#F8F8F8")):
            d.rectangle((i * 42 + 5, 33, i * 42 + 32, 174), fill=rgb)
    elif style == "tiny":
        d.rectangle((90, 90, 93, 93), fill="#050505")
    elif style == "faded":
        d.text((5, 9), "BLACK FONT", font=FONT, fill=(5, 5, 5, 115))
        d.text((5, 136), "WHITE FONT", font=FONT, fill=(255, 255, 255, 115))
    elif style != "empty":
        raise ValueError(style)
    output = BytesIO()
    im.save(output, "PNG")
    return output.getvalue()


def test_ciede2000_reference_and_identity():
    """Sharma et al.'s standard sample has Delta E00 approx 2.0425."""
    assert delta_e_2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) == pytest.approx(2.0425, abs=.0001)
    assert delta_e_2000((50, 2.6772, -79.7751), (50, 2.6772, -79.7751)) == 0
    assert delta_e_2000((50, 0, 0), (50, 0, 0)) == 0


@pytest.mark.parametrize("style", ("dark", "light", "mixed", "mixed-art",
                                    "red", "colorful", "tiny", "faded"))
def test_v10_is_bounded_partitioned_sha_guarded_and_deterministic(style):
    m = color_match.recommend_garment_colors(fixture(style), SHA)
    assert m and m["version"] == ALGORITHM == "colormatch-sms-c1717-v10"
    assert m["method"] == "role_aware_salience_ciede2000_v10"
    assert m["readability_check"] == "role_salience_alpha_composite_v4"
    assert 0 <= len(m["recommended"]) <= 10
    assert len(m["recommended"]) == len(set(m["recommended"]))
    assert len(m["review_colors"]) <= 5
    full = {key for key, _ in GARMENT_COLORS}
    assert set(m["eligible_colors"]) | set(m["review_colors"]) | set(m["unsafe_colors"]) == full
    assert set(m["review_colors"]) <= set(m["recommended"])
    assert set(m["recommended"]) <= set(m["eligible_colors"]) | set(m["review_colors"])
    assert not set(m["eligible_colors"]) & set(m["review_colors"])
    assert not set(m["unsafe_colors"]) & (set(m["recommended"]) | set(m["eligible_colors"]))
    assert len(m["color_reasons"]) == len(full)
    assert _validated_color_match({"color_match": m}, SHA)
    assert _validated_color_match({"color_match": m}, "b" * 64) is None
    assert color_match.recommend_garment_colors(fixture(style), SHA)["recommended"] == m["recommended"]


def test_dark_lettering_never_hidden_on_dark_garments():
    m = color_match.recommend_garment_colors(fixture("dark"), SHA)
    assert m and m["recommended"]
    for bad in ("black", "true-navy", "graphite", "pepper"):
        assert bad not in m["recommended"]
    assert "white" in m["eligible_colors"]


def test_white_lettering_on_dark_background():
    m = color_match.recommend_garment_colors(fixture("light"), SHA)
    assert m and m["recommended"]
    assert "white" not in m["recommended"]
    assert "true-navy" in m["eligible_colors"]


def test_multi_tone_typography_gets_review_options_without_making_them_safe(monkeypatch):
    # Real V9 behavior, not hand-written expectations. V9's strict gate
    # discards every garment on this particular test artwork.
    with monkeypatch.context() as ctx:
        ctx.setattr(color_match, "ALGORITHM", "colormatch-sms-c1717-v9")
        old = color_match.recommend_garment_colors(fixture("mixed"), SHA)
    new = color_match.recommend_garment_colors(fixture("mixed"), SHA)
    assert old and old["recommended"] == []
    assert new and len(new["recommended"]) >= 3
    assert new["review_colors"]
    assert all(x in new["recommended"] and x not in new["eligible_colors"]
               for x in new["review_colors"])


def test_red_text_not_hidden_by_red_shirt():
    m = color_match.recommend_garment_colors(fixture("red"), SHA)
    assert m
    assert "red" not in m["recommended"]
    assert "crimson" not in m["recommended"]
    assert "white" in m["eligible_colors"]


def test_truly_bad_or_unreadable_artwork_not_force_padded():
    m = color_match.recommend_garment_colors(fixture("faded"), SHA)
    assert m
    assert len(m["recommended"]) <= 5
    assert set(m["review_colors"]) <= set(m["recommended"])
    assert m["insufficient_safe_colors"]


def test_projector_rejects_forged_recommendations_and_reasons():
    m = color_match.recommend_garment_colors(fixture("mixed"), SHA)
    assert m and _validated_color_match({"color_match": m}, SHA)
    assert _validated_color_match({"color_match": {
        **m, "review_colors": ["fake"],
    }}, SHA) is None
    assert _validated_color_match({"color_match": {
        **m, "eligible_colors": m["eligible_colors"] + m["review_colors"],
    }}, SHA) is None
    assert _validated_color_match({"color_match": {
        **m, "unsafe_colors": m["unsafe_colors"] + m["recommended"][:1],
    }}, SHA) is None
    assert _validated_color_match({"color_match": {
        **m, "color_reasons": {**m["color_reasons"], "fake": "text_review"},
    }}, SHA) is None


def test_v9_stays_readable_until_v10_backfill(monkeypatch):
    with monkeypatch.context() as ctx:
        ctx.setattr(color_match, "ALGORITHM", "colormatch-sms-c1717-v9")
        legacy = color_match.recommend_garment_colors(fixture("dark"), SHA)
    assert legacy and _validated_color_match({"color_match": legacy}, SHA)


def test_single_pixel_artwork_does_not_crash_or_invent_print_ready_color():
    image = Image.new("RGBA", (192, 192))
    image.putpixel((96, 96), (10, 10, 10, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    meta = color_match.recommend_garment_colors(buffer.getvalue(), SHA)
    assert meta and not meta["recommended"]
    assert _validated_color_match({"color_match": meta}, SHA)


def test_completely_empty_png_is_not_an_artwork():
    assert color_match.recommend_garment_colors(fixture("empty"), SHA) is None
