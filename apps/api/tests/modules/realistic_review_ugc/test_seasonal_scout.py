"""Season-specific discovery in both CAM Scout workspaces."""
from app.modules.realistic_review_ugc.analysis import analysis_prompt
from app.modules.realistic_review_ugc.product_context import (
    derive_product_context_profile, product_visual_binding_fingerprint,
)
from app.modules.realistic_review_ugc.seasonal_scout import (
    detect_source_season, seasonal_hat_claim_search,
)


def test_christmas_gatorhats_claim_keeps_hat_front_and_original_search_terms():
    product = {
        "name": "custom-christmas-family-trucker-hat",
        "product_type": "trucker hat", "source_category": "embroidered hat",
    }
    profile = {
        "visual_context": {"status": "ready", "themes": ["holiday"]},
        "search_clusters": {
            "selfie_wearing_hat": ["hat mirror selfie"],
            "hand_holding_hat": ["cap held in hand"],
            "direct": ["person in trucker hat on vacation"],
            "adjacent": ["casual street"],
        },
    }
    result = seasonal_hat_claim_search(product, profile, ["hat mirror selfie", "family group"])
    assert result is not None
    queries, context = result
    assert len(queries) == 5
    assert all("Christmas" in item for item in queries[:3])
    assert all("cap" in item for item in queries[:3])
    assert "hat mirror selfie" in queries
    assert "family group" in queries
    assert context["search_clusters"]["selfie_wearing_hat"][0] == queries[0]
    assert context["search_clusters"]["hand_holding_hat"][0] == queries[1]
    assert context["search_clusters"]["direct"][0] == queries[2]
    assert context["search_clusters"]["adjacent"] == ["casual street"]
    assert profile["search_clusters"]["direct"] == ["person in trucker hat on vacation"]


def test_generic_holiday_gatorhats_does_not_inject_christmas():
    product = {"name": "custom-family-cap", "product_type": "cap"}
    context = {"visual_context": {"themes": ["holiday"]}}
    assert detect_source_season(product, context) is None
    assert seasonal_hat_claim_search(product, context, ["hat selfie"]) is None


def test_gatorhats_profile_preserves_specific_season_over_generic_holiday():
    for filename, season_label in [
        ("Christmas family embroidered cap", "Christmas"),
        ("Halloween funny embroidered cap", "Halloween"),
    ]:
        snapshot = {"name": filename, "product_type": "cap"}
        visual = {
            "status": "ready",
            "themes": ["holiday"],
            "scene_hints": ["casual living room"],
            "binding_fingerprint": product_visual_binding_fingerprint(snapshot, None),
            "summary": "Family embroidered holiday cap",
        }
        profile = derive_product_context_profile(
            product_snapshot=snapshot,
            config={"auto_context": True, "visual_context": visual},
        )
        direct = profile["search_clusters"]["direct"]
        assert direct
        assert season_label in direct[0]
        if season_label != "Christmas":
            assert all("Christmas" not in query for query in direct)


def test_reference_analysis_matches_season_only_as_context_not_garment_fit():
    snapshot = {"name": "christmas-family-sweatshirt", "product_type": "sweatshirt"}
    visual = {
        "status": "ready", "themes": ["holiday", "dad_family"],
        "summary": "Christmas embroidered family sweater",
        "binding_fingerprint": product_visual_binding_fingerprint(snapshot, None),
    }
    profile = derive_product_context_profile(
        product_snapshot=snapshot,
        config={"auto_context": True, "visual_context": visual},
    )
    prompt = analysis_prompt({
        **snapshot,
        "source_category": "embroidered apparel",
        "discovery_context": profile,
    })
    assert "artwork-specific seasonal context: Christmas" in prompt
    assert "decorated Christmas tree" in prompt
    assert "context_match_score" in prompt
    assert "do not change product_fit_score" in prompt
    assert "TECHNICAL GARMENT-FIT score" in prompt
