"""TikTokShop apparel placement-awareness without extra provider calls."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.realistic_review_ugc.apparel_placement import (
    CandidateApparelZone, SourceApparelPlacement, apparel_placement_queries,
    evaluate_apparel_suitability, source_placements,
)
from app.modules.realistic_review_ugc.analysis import (
    APPAREL_ANALYZER_VERSION, ANALYZER_VERSION, ReferenceAnalysisDocument,
    analysis_prompt,
)
from app.modules.realistic_review_ugc.product_context import (
    PRODUCT_VISUAL_CONTEXT_VERSION, TIKTOKSHOP_APPAREL_CONTEXT_VERSION,
    ProductVisualContextDocument, derive_product_context_profile,
    product_visual_binding_fingerprint, product_visual_context_prompt,
)


def source(zone: str, priority="primary"):
    return SourceApparelPlacement(zone=zone, priority=priority, confidence=0.94)


def observation(zone: str, vis=0.95, occ=0.06, conf=0.96):
    return CandidateApparelZone(
        zone=zone, visible_ratio=vis, occlusion_ratio=occ, confidence=conf,
    )


def judge(placements, zones, view="front", frame=0.43):
    return evaluate_apparel_suitability(
        placements=placements, garment_view=view,
        garment_frame_ratio=frame, observations=zones,
    )


@pytest.mark.parametrize("zone", [
    "collar", "center_chest", "left_chest", "right_chest",
    "left_sleeve", "right_sleeve", "left_upper_arm", "right_upper_arm",
])
def test_each_embroidery_zone_passes_only_when_visible_and_clear(zone):
    result = judge([source(zone)], [observation(zone)])
    assert result.status is None and result.score > 0.8
    blocked = judge([source(zone)], [observation(zone, occ=0.85)])
    assert blocked.status == "rejected_context"
    assert blocked.reason == "APPAREL_PRIMARY_OCCLUDED"


@pytest.mark.parametrize("zone", ["center_chest", "collar", "left_sleeve", "right_upper_arm"])
def test_cropped_or_wrong_side_is_not_automatically_approved(zone):
    result = judge([source(zone)], [observation(zone, vis=0.20)])
    assert result.status == "rejected_context"
    assert result.reason == "APPAREL_PRIMARY_NOT_VISIBLE"
    no_zone = judge([source(zone)], [observation("right_sleeve")])
    assert no_zone.status == "needs_review"
    assert no_zone.reason == "APPAREL_PRIMARY_UNCERTAIN"


def test_back_view_and_tiny_shirt_are_rejected_even_when_artwork_zone_looks_clear():
    zones = [observation("center_chest")]
    assert judge([source("center_chest")], zones, view="back").reason == "APPAREL_WRONG_VIEW_BACK"
    assert judge([source("center_chest")], zones, frame=0.08).reason == "APPAREL_GARMENT_TOO_SMALL"


def test_multi_zone_no_auto_approval_when_sleeve_or_collar_is_missing():
    placements = [source("center_chest"), source("collar", "secondary"),
                  source("left_sleeve", "secondary")]
    valid = judge(placements, [observation(p.zone) for p in placements], view="three_quarter")
    assert valid.status is None and valid.score > 0.70
    missing_collar = judge(placements, [
        observation("center_chest"), observation("left_sleeve"),
    ])
    assert missing_collar.status == "needs_review"
    covered_sleeve = judge(placements, [
        observation("center_chest"), observation("collar"),
        observation("left_sleeve", occ=0.9),
    ])
    assert covered_sleeve.status == "needs_review"
    assert covered_sleeve.reason == "APPAREL_SECONDARY_OCCLUDED"


def test_unseen_design_zone_requires_review_instead_of_guessing():
    assert judge([], [], view="front").reason == "APPAREL_PLACEMENT_UNKNOWN"
    assert judge([source("collar")], [observation("collar", conf=0.2)]).reason == "APPAREL_PRIMARY_UNCERTAIN"


def test_source_placements_normalize_duplicate_primary_and_ignore_low_confidence():
    result = source_placements({"apparel_placements": [
        {"zone": "center_chest", "priority": "primary", "confidence": 0.99},
        {"zone": "collar", "priority": "primary", "confidence": 0.85},
        {"zone": "left_sleeve", "priority": "secondary", "confidence": 0.40},
        {"zone": "center_chest", "priority": "secondary", "confidence": 0.91},
        {"zone": "invalid", "priority": "primary", "confidence": 0.95},
    ]})
    assert [(p.zone, p.priority) for p in result] == [
        ("center_chest", "primary"), ("collar", "secondary"),
    ]
    assert "neckline visible" in apparel_placement_queries("sweatshirt", result)[1]


def test_vision_source_and_candidate_prompts_share_the_exact_zone_contract():
    product = {
        "name": "Mom Sweatshirt", "product_type": "sweatshirt",
        "source_category": "embroidered apparel",
    }
    document = ProductVisualContextDocument.model_validate({
        "themes": ["mom_family"], "embroidery_text": ["MAMA"],
        "apparel_placements": [
            {"zone": "center_chest", "priority": "primary", "confidence": 0.99},
            {"zone": "right_sleeve", "priority": "secondary", "confidence": 0.87},
        ],
        "confidence": 0.92, "summary": "Embroidered sweatshirt",
    })
    visual = {
        "status": "ready", "version": TIKTOKSHOP_APPAREL_CONTEXT_VERSION,
        "binding_fingerprint": product_visual_binding_fingerprint(product, None),
        "apparel_placements": [p.model_dump() for p in document.apparel_placements],
    }
    profile = derive_product_context_profile(
        product_snapshot=product, campaign_name=product["name"],
        config={"visual_context": visual},
    )
    assert [x["zone"] for x in profile["apparel_placements"]] == ["center_chest", "right_sleeve"]
    assert "front upper body chest unobstructed" in profile["search_clusters"]["direct"][0]
    assert "arm sleeves visible" in profile["search_clusters"]["direct"][1]
    source_prompt = product_visual_context_prompt(product, view_type="embroidery_closeup")
    candidate_prompt = analysis_prompt({**product, "discovery_context": profile})
    assert "wearer's" in source_prompt.lower()
    assert "center_chest (primary)" in candidate_prompt
    assert "right_sleeve (secondary)" in candidate_prompt
    assert "garment_frame_ratio" in candidate_prompt
    assert "hat-first" not in candidate_prompt
    assert APPAREL_ANALYZER_VERSION != ANALYZER_VERSION
    assert TIKTOKSHOP_APPAREL_CONTEXT_VERSION != PRODUCT_VISUAL_CONTEXT_VERSION


def test_same_lettering_different_placement_never_shares_a_tiktokshop_scout_pool():
    from app.modules.realistic_review_ugc.source_plans import source_plan_embroidery_signature
    from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
    visual = {
        "status": "ready", "embroidery_text": ["MAMA"],
        "embroidery_identity": "MAMA text embroidery",
        "apparel_placements": [
            {"zone": "center_chest", "priority": "primary", "confidence": 0.95},
        ],
    }
    chest = source_plan_embroidery_signature(visual, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID)
    arm = source_plan_embroidery_signature(
        {**visual, "apparel_placements": [{
            "zone": "left_sleeve", "priority": "primary", "confidence": 0.95,
        }]}, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
    )
    hat_chest = source_plan_embroidery_signature(visual, "gatorhats-root")
    hat_arm = source_plan_embroidery_signature(
        {**visual, "apparel_placements": [{
            "zone": "left_sleeve", "priority": "primary", "confidence": 0.95,
        }]}, "gatorhats-root",
    )
    assert chest and arm and chest != arm
    assert hat_chest == hat_arm


def test_gatorhats_keeps_hat_specific_prompts_without_apparel_scoring():
    product = {"product_type": "cap", "source_category": "embroidered hat"}
    assert "hat product-review" in analysis_prompt(product).lower()
    assert "APPAREL DESIGN-PLACEMENT EXTRACTION" not in product_visual_context_prompt(
        product, view_type="embroidery_closeup",
    )
    assert source_placements({"apparel_placements": []}) == []


def test_schema_rejects_unknown_zones_and_out_of_range_confidence():
    with pytest.raises(ValidationError):
        SourceApparelPlacement(zone="back", confidence=0.8)
    with pytest.raises(ValidationError):
        CandidateApparelZone(zone="collar", visible_ratio=1.3,
                             occlusion_ratio=0.1, confidence=0.8)
    # Optional fields keep existing non-TikTokShop reference fixtures intact.
    assert "garment_view" in ReferenceAnalysisDocument.model_fields
