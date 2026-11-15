"""Deterministic per-artwork SMS sales-informed C1717 color recommendations.

This is a static 60-day demand prior, NOT a live Best sellers report.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from app.modules.etsy_spy.color_match import recommend_garment_colors
from app.modules.etsy_spy.color_match_palette import (
    ALGORITHM, GARMENT_COLORS, SALES_SPEED_PRIOR, SALES_PRIOR_SNAPSHOT,
)
from app.modules.etsy_spy.design_artifacts import _validated_color_match


def _thumbnail(inks: tuple[str, ...]) -> bytes:
    im = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    for i, ink in enumerate(inks):
        left = 14 + 164 * i // len(inks)
        right = 14 + 164 * (i + 1) // len(inks)
        ImageDraw.Draw(im).rectangle((left, 18, right, 172), fill=ink)
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_popularity_prior_is_bounded_and_not_live_inventory():
    assert ALGORITHM == "colormatch-sms-c1717-v7"
    assert SALES_PRIOR_SNAPSHOT == "sms-c1717-60d-20261114"
    available = {name for name, _ in GARMENT_COLORS}
    assert set(SALES_SPEED_PRIOR).issubset(available)
    assert all(0 <= value <= 1 for value in SALES_SPEED_PRIOR.values())
    assert SALES_SPEED_PRIOR["pepper"] > SALES_SPEED_PRIOR["blue-spruce"]
    assert SALES_SPEED_PRIOR["blue-spruce"] > SALES_SPEED_PRIOR["blue-jean"]
    assert SALES_SPEED_PRIOR["blue-jean"] > SALES_SPEED_PRIOR["denim"]


def test_seven_to_ten_distinct_safe_choices_for_six_artwork_palettes():
    sha = "a" * 64
    cases = {
        "white": ("#FFFFFF",),
        "black": ("#101010",),
        "cream-red": ("#F6EDDA", "#9E2525"),
        "pink-blue": ("#FFB7CD", "#3F66A3"),
        "multi": ("#FFFFFF", "#111111", "#FF4234", "#0B8652"),
        "grey": ("#777777",),
    }
    allowed = {name for name, _ in GARMENT_COLORS}
    first = {}
    for name, inks in cases.items():
        preview = _thumbnail(inks)
        got = recommend_garment_colors(preview, sha)
        assert got is not None, name
        first[name] = got["recommended"][0]
        assert got["version"] == ALGORITHM
        assert got["popularity_source"] == SALES_PRIOR_SNAPSHOT
        assert got["method"] == "design_region_alpha_sales_prior"
        assert got["insufficient_safe_colors"] is (len(got["recommended"]) < 7)
        assert 0 <= len(got["recommended"]) <= 10, name
        assert len(got["recommended"]) == len(set(got["recommended"])), name
        assert set(got["recommended"]) <= allowed, name
        assert set(got["recommended"]).isdisjoint(got["unsafe_colors"]), name
        assert set(got["scores"]) == set(got["recommended"])
        assert set(got["contrast_scores"]) == set(got["recommended"])
        assert _validated_color_match({"color_match": got}, sha)
        assert recommend_garment_colors(preview, sha)["recommended"] == got["recommended"]
    assert first["white"] != first["black"]
    assert "white" not in recommend_garment_colors(_thumbnail(cases["white"]), sha)["recommended"]
    assert "black" not in recommend_garment_colors(_thumbnail(cases["black"]), sha)["recommended"]


def test_v4_cached_recommendations_remain_visible_during_rollout():
    sha = "b" * 64
    legacy = {
        "version": "colormatch-sms-c1717-v4",
        "output_sha256": sha,
        "recommended": [name for name, _ in GARMENT_COLORS][:16],
        "method": "thumbnail_pixel_contrast",
    }
    assert _validated_color_match({"color_match": legacy}, sha)
    assert _validated_color_match({"color_match": legacy}, "a" * 64) is None
