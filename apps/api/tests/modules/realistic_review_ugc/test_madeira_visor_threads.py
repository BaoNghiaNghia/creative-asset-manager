"""Keep supplied GatorHats visor-to-Madeira codes consistent across both stages."""
from pathlib import Path

import pytest

from app.modules.realistic_review_ugc.madeira_visor_threads import (
    MADEIRA_VISOR_THREADS, PALETTE_VERSION, UNMAPPED_VISORS,
    stage1_madeira_guidance,
)
from app.modules.realistic_review_ugc.visor_accent import (
    VISOR_THREAD_RGB, LEGACY_VISOR_THREAD_RGB, LEGACY_PALETTE_VERSION,
    MADEIRA_PALETTE_VERSION,
)
from app.modules.realistic_review_ugc.creative_v3_generation import (
    CreativeV3Error, prompt_for_slot, verify_snapshot, snapshot_for_job,
)
from app.modules.realistic_review_ugc.heritage_lettering import HERITAGE_VERSION
from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION
from app.modules.realistic_review_ugc.creative_v3_generation import _brief_plan


EXPECTED = {
    "natural-royal": (1829, "Blue", "#2464A7"),
    "natural-forest-green": (1750, "Emerald Green", "#006C45"),
    "natural-brown": (1859, "Dark Brown", "#36302E"),
    "natural-maroon": (1784, "Maroon", "#5E1E28"),
    "natural-charcoal": (1841, "Dark Gray", "#3E4B50"),
    "natural-khaki": (1729, "Light Brown", "#907663"),
    "natural-camo-green": (1750, "Emerald Green", "#006C45"),
    "natural-black": (1800, "Black", "#202424"),
    "natural-navy": (1967, "Navy", "#182C48"),
    "natural-red": (1637, "Red", "#AC211C"),
}


def test_supplied_madeira_codes_and_rgb_match_bundled_supplier_catalog():
    root = Path(__file__).resolve().parents[5]
    catalog = root / "deploy/codex/skills/gatorhats-8869-image-studio/references/mauchi.txt"
    suppliers = {}
    for line in catalog.read_text(encoding="utf-8").splitlines():
        code, series, name, family, red, green, blue = line.strip().split(",")
        assert series == "SMS Madeira PolyNeon"
        suppliers[int(code)] = (name, (int(red), int(green), int(blue)))
    assert len(EXPECTED) == len(MADEIRA_VISOR_THREADS) == 10
    assert len({item.code for item in MADEIRA_VISOR_THREADS.values()}) == 9
    for key, (number, name, hex_value) in EXPECTED.items():
        thread = MADEIRA_VISOR_THREADS[key]
        assert (thread.code, thread.name, thread.hex) == (number, name, hex_value)
        assert thread.rgb == suppliers[number][1]
        assert thread.rgb == VISOR_THREAD_RGB[key]
        assert name == suppliers[number][0]
    assert set(VISOR_THREAD_RGB) == set(EXPECTED) | UNMAPPED_VISORS
    assert len(UNMAPPED_VISORS) == 2
    assert all(key not in MADEIRA_VISOR_THREADS for key in UNMAPPED_VISORS)
    assert VISOR_THREAD_RGB["natural-forest-green"] == VISOR_THREAD_RGB["natural-camo-green"]
    # The UI's compile-time catalog cannot drift from the verified backend
    # mapping or SKU numbering without breaking this test.
    frontend = (root / "apps/client/app/realistic-review-ugc/BlueprintEmbroideryTint.ts").read_text()
    for key, (number, name, hex_value) in EXPECTED.items():
        suffix = key.removeprefix("natural-")
        js_key = f'"{suffix}"' if "-" in suffix else suffix
        assert (
            f'{js_key}: {{ code: {number}, name: "{name}", hex: "{hex_value}" }}'
            in frontend
        )


def test_stage1_uses_pinned_catalog_only_when_explicitly_selected():
    from app.modules.realistic_review_ugc.creative_v3_generation import SCHEMA
    # Build the pinned snapshot without any DB or provider access.
    phrase = "GOD BLESS AMERICA"
    source = {
        "keyword_exact": phrase,
        "strategy": "heritage", "meaning_emotion": "hope",
        "target_audience": "cap lovers", "purchase_motivation": "belief",
        "creative_hook": "lettering",
        "concepts": [],
    }
    for mode in ("safe", "creative_twist", "explore"):
        source["concepts"].append({
            "name": mode, "mode": mode, "description": "letters",
            "familiar_format": "editorial", "creative_twist": "type",
            "creative_hook": f"unique-{mode}-hook", "typography_plan": "two lines",
            "embroidery_plan": "satin", "clarity_assessment": "review",
            "embroidery_fit": "review", "risks": ["tiny"],
            "design_variants": [f"{mode} {i}" for i in range(1 if mode == "safe" else 4)],
        })
    plan = _brief_plan(
        keyword=phrase, brief_json=source, prompt_version=PROMPT_VERSION, brief_id="b-1",
    )
    assert plan["identity"]["schema_version"] == SCHEMA
    base = prompt_for_slot(
        plan, 1, design_style="heritage", design_style_version=HERITAGE_VERSION,
    )
    pinned = prompt_for_slot(
        plan, 1, design_style="heritage", design_style_version=HERITAGE_VERSION,
        thread_palette_version=PALETTE_VERSION,
    )
    assert "MADEIRA POLYNEON VERIFIED" not in base
    assert stage1_madeira_guidance() in pinned
    assert "1829 Blue (#2464A7)" in pinned
    assert "1750 Emerald Green (#006C45)" in pinned
    assert "do NOT auto-match" in pinned
    snapshot = {**snapshot_for_job(plan), "design_style": "heritage",
                "design_style_version": HERITAGE_VERSION,
                "thread_palette_version": PALETTE_VERSION}
    verify_snapshot(plan, snapshot)
    verify_snapshot(plan, {k: v for k, v in snapshot.items()
                           if k != "thread_palette_version"})
    with pytest.raises(CreativeV3Error, match="palette"):
        verify_snapshot(plan, {**snapshot, "thread_palette_version": "unknown"})
    with pytest.raises(CreativeV3Error, match="palette"):
        prompt_for_slot(
            plan, 1, design_style="heritage", design_style_version=HERITAGE_VERSION,
            thread_palette_version="unknown",
        )


def test_legacy_visor_palette_is_preserved_for_queued_jobs_without_version():
    assert MADEIRA_PALETTE_VERSION == PALETTE_VERSION
    assert LEGACY_PALETTE_VERSION != PALETTE_VERSION
    assert LEGACY_VISOR_THREAD_RGB["natural-royal"] == (45, 81, 157)
    assert VISOR_THREAD_RGB["natural-royal"] == (36, 100, 167)
    assert LEGACY_VISOR_THREAD_RGB["natural-khaki"] == (110, 79, 48)
    assert VISOR_THREAD_RGB["natural-khaki"] == (144, 118, 99)
