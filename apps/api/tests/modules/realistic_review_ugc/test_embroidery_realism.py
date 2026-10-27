"""Regressions for the shared direct-thread contract across all seven Product slots."""
from __future__ import annotations

import pytest

from app.modules.realistic_review_ugc.embroidery_realism import (
    EMBROIDERY_REALISM_VERSION,
    covered_product_slot_stages,
    embroidery_prompt_for_product_slot,
)
from app.modules.realistic_review_ugc.ecommerce_pipeline import (
    SCENE_GROUPS, VIEW_PROMPTS,
)


@pytest.mark.parametrize(
    ("stage", "slot_label"),
    [
        ("prod_s2_ec1", "SLOT 1"),
        ("prod_s2_ec2", "SLOT 2"),
        ("prod_s2_ec3", "SLOT 3"),
        ("prod_s2_ec4", "SLOT 4"),
        ("prod_s2_mh_1", "SLOT 5"),
        ("prod_s2_mh_2", "SLOT 5"),
        ("prod_s2_mh_3", "SLOT 5"),
        ("prod_s2_ugc_1", "SLOT 6"),
        ("prod_s2_ugc_2", "SLOT 6"),
        ("prod_s2_ugc_3", "SLOT 6"),
        ("prod_s2_col", "SLOT 7"),
    ],
)
def test_each_future_products_stage2_image_uses_same_physical_thread_spec(stage, slot_label):
    prompt = embroidery_prompt_for_product_slot(stage)
    assert EMBROIDERY_REALISM_VERSION in prompt
    assert slot_label in prompt
    for requirement in (
        "genuine LOW-PROFILE direct machine embroidery",
        "fine individual thread filaments",
        "moderate stitch density",
        "slight local fabric tension",
        "soft micro-shadow",
        "puff/3D foam",
        "rubber/plastic/vinyl",
        "oversharpening",
        "PRE-SAVE VISUAL QUALITY CHECK",
        "Reject the candidate",
        "source artwork",
    ):
        assert requirement.casefold() in prompt.casefold()


def test_all_six_scene_variants_and_four_ecommerce_slots_are_covered():
    stages = set(VIEW_PROMPTS)
    assert stages == covered_product_slot_stages() - {"prod_s2_col"}
    assert len(SCENE_GROUPS["prod_s2_mh"]) == 3
    assert len(SCENE_GROUPS["prod_s2_ugc"]) == 3


def test_fabric_first_refinement_is_strictly_scoped_to_slot_one():
    front = embroidery_prompt_for_product_slot("prod_s2_ec1")
    assert "SLOT-1-ONLY FABRIC-FIRST PHOTO EDIT" in front
    assert "transparent Stage 1 source design" in front
    assert "do NOT repaint the full cap" in front
    for stage in covered_product_slot_stages() - {"prod_s2_ec1"}:
        assert "SLOT-1-ONLY FABRIC-FIRST PHOTO EDIT" not in embroidery_prompt_for_product_slot(stage)


def test_rear_remains_blank_not_forced_to_have_front_artwork():
    prompt = embroidery_prompt_for_product_slot("prod_s2_ec4")
    assert "UNEMBROIDERED rear crown" in prompt
    assert "No front lettering on the back" in prompt
    assert "do not invent or reveal any front text" in prompt
    assert "official stock" in prompt


def test_stock_colors_and_multi_hat_artwork_survive_quality_override():
    multi = embroidery_prompt_for_product_slot("prod_s2_mh_1")
    assert "same authentic fine, low-profile" in multi
    assert "Preserve the scene's exact required number of hats" in multi
    colorway = embroidery_prompt_for_product_slot("prod_s2_col")
    assert "TWELVE STOCK COLORS" in colorway
    assert "Never remap colored embroidery to brown/sepia" in colorway


def test_ugc_does_not_exaggerate_thread_texture_for_distant_photos():
    scene = embroidery_prompt_for_product_slot("prod_s2_ugc_1")
    assert "Let thread detail resolve naturally" in scene
    assert "actual daylight and camera distance" in scene


def test_missing_stage_fails_closed_to_prevent_unintended_review_workflow_changes():
    for stage in ("rev_s2", "stage2", "prod_s2_mh", "wrong"):
        with pytest.raises(ValueError, match="Unsupported GatorHats Products slot"):
            embroidery_prompt_for_product_slot(stage)
