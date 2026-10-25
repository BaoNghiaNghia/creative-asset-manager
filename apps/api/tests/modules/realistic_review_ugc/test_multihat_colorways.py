"""Multi-hat palettes are varied per design, pinned per job and stock verified."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from app.modules.realistic_review_ugc import multihat_colorways as palettes


@pytest.fixture
def fake_verified_stock(monkeypatch):
    image = Image.new("RGB", (720, 560), "#efe8d7")
    output = BytesIO()
    image.save(output, format="JPEG")
    sample = output.getvalue()
    monkeypatch.setattr(palettes, "_stock_bytes", lambda _settings, _color: sample)
    return sample


def choose(design: str, *, run: str = "run-1", available=None, pinned=None):
    return palettes.choose_palettes(
        tenant_id="tenant-A", source_plan_id=design,
        source_revision="revision-42", available=available or list(palettes.COLOR_NAMES),
        pinned=pinned, run_key=run,
    )


def test_distinct_official_colors_for_four_four_three_hats_per_design():
    a = choose("design-A")
    replay = choose("design-A")
    b = choose("design-B")
    new_run = choose("design-A", run="run-2")
    assert a == replay
    assert a != b
    assert a != new_run
    assert {key: len(value) for key, value in a.items()} == {
        "prod_s2_mh_1": 4,
        "prod_s2_mh_2": 4,
        "prod_s2_mh_3": 3,
    }
    all_colors = []
    for stage, colors in a.items():
        assert palettes.validated_palette(stage, colors) == colors
        assert len(colors) == len(set(colors))
        all_colors.extend(colors)
    # Contrast per shot takes priority over global uniqueness: all 13 official
    # colors yield warm/blue/green/neutral for 05/06 and warm/blue/green for 07.
    assert len(all_colors) == 11
    assert len(set(all_colors)) >= 9
    for stage, colors in a.items():
        assert len({palettes.COLOR_FAMILIES[color] for color in colors}) == len(colors)


def test_retry_pins_existing_palette_while_filling_missing_shots():
    original = choose("design-A")
    replay = choose("design-A", run="different-run", pinned={
        "prod_s2_mh_1": original["prod_s2_mh_1"],
        "prod_s2_mh_2": original["prod_s2_mh_2"],
    })
    assert replay["prod_s2_mh_1"] == original["prod_s2_mh_1"]
    assert replay["prod_s2_mh_2"] == original["prod_s2_mh_2"]
    assert len(set(replay["prod_s2_mh_3"])) == 3


def test_scarce_stocks_repeat_between_photos_but_never_within():
    subset = list(palettes.COLOR_NAMES)[:4]
    p = choose("design-C", available=subset)
    for stage, colors in p.items():
        assert len(colors) == palettes.STAGE_CAP_COUNTS[stage]
        assert len(colors) == len(set(colors))
        assert set(colors) <= set(subset)
    with pytest.raises(palettes.MultiHatColorwayError, match="four"):
        choose("design-C", available=subset[:3])


def test_explicit_regenerate_changes_colors_while_preserving_visual_family():
    previous = ["natural-red", "natural-royal", "natural-forest-green", "natural-black"]
    updated = palettes.different_regeneration_palette(
        "prod_s2_mh_1", previous, previous, list(palettes.COLOR_NAMES),
    )
    assert len(updated) == 4
    assert len(set(updated)) == 4
    assert set(updated) != set(previous)
    assert {palettes.COLOR_FAMILIES[key] for key in updated} == {
        "warm", "blue", "green", "neutral",
    }


def test_no_unapproved_or_duplicate_palettes():
    stage = "prod_s2_mh_1"
    assert not palettes.validated_palette(stage, ["natural-red"] * 4)
    assert not palettes.validated_palette(stage, ["natural-red", "natural-navy", "fake-color", "natural-royal"])
    assert not palettes.validated_palette(stage, ["natural-black", "natural-red"])
    assert not palettes.validated_palette("not-a-scene", ["natural-black"] * 4)


def test_worker_uses_verified_pinned_hashes_and_board(fake_verified_stock, monkeypatch):
    colors = choose("design-A")["prod_s2_mh_1"]
    hashes = palettes.pinned_stock_hashes(None, "prod_s2_mh_1", colors)
    assert all(digest == sha256(fake_verified_stock).hexdigest() for digest in hashes.values())
    selected = palettes.checked_palette_stock(None, "prod_s2_mh_1", colors, hashes)
    board = palettes.make_palette_board("prod_s2_mh_1", selected)
    assert board.role == "official_multicolor_cap_reference"
    assert (board.image.width, board.image.height) == (1500, 1320)
    text = palettes.multihat_color_prompt("prod_s2_mh_1", colors)
    assert "EXACTLY 4 different" in text
    assert "same text, layout, stitching and thread colors" in text
    assert "ONLY the hat's physical" in text
    for key in colors:
        assert key in text

    tampered = dict(hashes)
    tampered[colors[0]] = "0" * 64
    with pytest.raises(palettes.MultiHatColorwayError, match="changed"):
        palettes.checked_palette_stock(None, "prod_s2_mh_1", colors, tampered)
    with pytest.raises(palettes.MultiHatColorwayError, match="Missing per-color"):
        palettes.checked_palette_stock(None, "prod_s2_mh_1", colors, {})


def test_three_hat_prompt_is_independently_pinned():
    colors = choose("design-D")["prod_s2_mh_3"]
    prompt = palettes.multihat_color_prompt("prod_s2_mh_3", colors)
    assert "EXACTLY 3 different" in prompt
    assert "hat 3:" in prompt
    assert "hat 4:" not in prompt
