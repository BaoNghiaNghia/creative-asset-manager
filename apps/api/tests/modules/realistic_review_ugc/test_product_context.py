from app.modules.realistic_review_ugc.analysis import (
    analysis_prompt,
    product_context_matching_active,
)
from app.modules.realistic_review_ugc.keyword_strategy import build_campaign_search_queries
from app.modules.realistic_review_ugc.product_context import (
    ProductVisualContextDocument,
    context_feedback_ranking_signal,
    derive_context_feedback_learning,
    derive_product_context_profile,
    merge_product_visual_context,
    product_visual_binding_fingerprint,
    select_product_visual_references,
)
from app.modules.realistic_review_ugc.scout_automation import adaptive_search_queries


def test_visual_context_normalizes_gemini_confidence_labels():
    assert ProductVisualContextDocument.model_validate(
        {
            "themes": ["outdoor"],
            "scene_hints": ["weekend outing"],
            "audience_hints": [],
            "occasion_hints": [],
            "product_cues": ["embroidered mountain"],
            "avoid_hints": [],
            "confidence": "high",
            "summary": "Outdoor embroidery suggests a casual weekend context.",
        }
    ).confidence == 0.9
    assert ProductVisualContextDocument.model_validate(
        {
            "themes": [],
            "scene_hints": [],
            "audience_hints": [],
            "occasion_hints": [],
            "product_cues": [],
            "avoid_hints": [],
            "confidence": "75%",
            "summary": "Limited visual evidence.",
        }
    ).confidence == 0.75


def test_visual_scene_hints_rank_ahead_of_generic_theme_templates():
    product = {
        "id": "grandpa-golf-cap",
        "revision": 3,
        "name": "Best Grandpa By Par",
        "product_type": "cap",
    }
    visual = {
        "status": "ready",
        "binding_fingerprint": product_visual_binding_fingerprint(product, None),
        "themes": ["dad_family", "sports"],
        "scene_hints": [
            "grandfather golf course outing",
            "dad golfing weekend",
            "golf course lifestyle",
        ],
        "audience_hints": ["grandfather", "dad", "golfer"],
        "occasion_hints": ["Father's Day gift", "golf outing"],
        "product_cues": [
            "embroidered golf swing figure",
            "text reading Best Grandpa By Par",
        ],
        "avoid_hints": [],
        "confidence": 0.95,
        "summary": "Grandpa golf embroidery.",
    }

    profile = derive_product_context_profile(
        product_snapshot=product,
        campaign_name="Best Grandpa By Par",
        config={"auto_context": True, "visual_context": visual},
    )

    assert profile["search_clusters"]["direct"][:3] == [
        "grandfather golf course outing candid phone photo",
        "dad golfing weekend candid phone photo",
        "golf course lifestyle candid phone photo",
    ]

    queries = build_campaign_search_queries(
        name="Best Grandpa By Par",
        queries=profile["search_clusters"]["direct"][:2],
        protected_queries=profile["search_clusters"]["direct"][:2],
        product_snapshot=product,
        discovery_mode="product_context",
        product_context=profile,
        max_queries=10,
    )
    assert queries[:3] == [
        "grandfather golf course outing candid phone photo",
        "dad golfing weekend candid phone photo",
        "golf course lifestyle candid phone photo",
    ]


def test_product_context_detects_pet_owner_and_builds_scene_clusters():
    profile = derive_product_context_profile(
        product_snapshot={
            "name": "Custom Dog Portrait Embroidered Hat",
            "source_description": "Personalized pet portrait gift for dog owners",
            "product_type": "cap",
        },
        campaign_name="Pet portrait lifestyle",
        queries=["dog owner lifestyle"],
        config={"auto_context": True},
    )

    assert "pet_owner" in profile["themes"]
    assert profile["source"]["product_type"] == "cap"
    assert any(
        "dog" in query.lower()
        for query in profile["search_clusters"]["direct"]
    )


def test_product_context_keeps_anchors_and_adds_contextual_queries():
    profile = derive_product_context_profile(
        product_snapshot={
            "name": "Dad Bicycle Embroidered Cap",
            "source_description": "Personalized gift for dad who loves cycling",
        },
        campaign_name="Dad cycling reference",
        queries=["dad outdoor lifestyle"],
        config={"auto_context": True},
    )

    queries = build_campaign_search_queries(
        name="Dad cycling reference",
        queries=["dad outdoor lifestyle"],
        protected_queries=["dad outdoor lifestyle"],
        product_snapshot={"name": "Dad Bicycle Embroidered Cap"},
        discovery_mode="product_context",
        product_context=profile,
        max_queries=10,
    )

    assert queries[0] == "dad outdoor lifestyle"
    assert len(queries) <= 10
    assert any(
        "dad" in query.lower() or "father" in query.lower()
        for query in queries[1:]
    )
    assert any(
        "bicycle" in query.lower() or "cyclist" in query.lower()
        for query in queries[1:]
    )


def test_context_feedback_ranks_good_context_above_wrong_context():
    profile = {
        "search_clusters": {
            "direct": [
                "woman with dog candid phone photo",
                "man with dog casual outdoor lifestyle",
            ],
            "adjacent": [],
            "generic": [],
        }
    }
    queries = build_campaign_search_queries(
        name="Pet",
        queries=["pet lifestyle"],
        protected_queries=[],
        discovery_mode="product_context",
        product_context=profile,
        outcomes=[
            (
                "woman with dog candid phone photo",
                "approved",
                None,
                "good",
            ),
            (
                "man with dog casual outdoor lifestyle",
                "approved",
                None,
                "wrong",
            ),
        ],
        max_queries=3,
    )

    assert queries.index("woman with dog candid phone photo") < queries.index(
        "man with dog casual outdoor lifestyle"
    )


def test_adaptive_search_uses_context_feedback(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.9,
    )
    queries = [
        "woman with dog candid phone photo",
        "man with dog casual outdoor lifestyle",
    ]
    outcomes = [
        *[
            (
                "woman with dog candid phone photo",
                "approved",
                None,
                "good",
            )
            for _ in range(4)
        ],
        *[
            (
                "man with dog casual outdoor lifestyle",
                "approved",
                None,
                "wrong",
            )
            for _ in range(4)
        ],
    ]

    ranked = adaptive_search_queries(queries, [], outcomes)

    assert ranked[0] == "woman with dog candid phone photo"
    assert "man with dog casual outdoor lifestyle" not in ranked


def test_saved_profile_does_not_promote_detected_themes_to_operator_themes():
    first = derive_product_context_profile(
        product_snapshot={"name": "Dog portrait cap"},
        config={"auto_context": True},
    )
    assert first["operator_themes"] == []
    assert "pet_owner" in first["detected_themes"]

    refreshed = derive_product_context_profile(
        product_snapshot={"name": "Teacher apple pencil cap"},
        config=first,
    )
    assert "pet_owner" not in refreshed["themes"]
    assert "teacher_school" in refreshed["themes"]


def test_visual_context_enriches_profile_when_binding_matches():
    product = {
        "id": "product-1",
        "revision": 3,
        "name": "Custom embroidered cap",
        "product_type": "cap",
    }
    references = [{
        "id": "ref-1",
        "variant_id": None,
        "view_type": "front",
        "version": 1,
        "content_hash": "hash-a",
        "remote_file_id": "drive-1",
    }]
    visual = {
        "status": "ready",
        "themes": ["pet_owner"],
        "scene_hints": ["dog owner park"],
        "references_analyzed": 1,
        "binding_fingerprint": product_visual_binding_fingerprint(
            product,
            references,
        ),
    }

    profile = derive_product_context_profile(
        product_snapshot=product,
        reference_snapshot=references,
        config={
            "auto_context": True,
            "visual_context": visual,
        },
    )

    assert profile["visual_context"]["status"] == "ready"
    assert "pet_owner" in profile["themes"]
    assert any(
        "dog owner park" in query
        for query in profile["search_clusters"]["direct"]
    )


def test_visual_context_becomes_stale_and_stops_influencing_profile():
    product = {
        "id": "product-1",
        "revision": 3,
        "name": "Custom embroidered cap",
        "product_type": "cap",
    }
    original_references = [{
        "id": "ref-1",
        "variant_id": None,
        "view_type": "front",
        "version": 1,
        "content_hash": "hash-a",
        "remote_file_id": "drive-1",
    }]
    changed_references = [{
        **original_references[0],
        "version": 2,
        "content_hash": "hash-b",
    }]

    profile = derive_product_context_profile(
        product_snapshot=product,
        reference_snapshot=changed_references,
        config={
            "auto_context": True,
            "visual_context": {
                "status": "ready",
                "themes": ["pet_owner"],
                "scene_hints": ["dog owner park"],
                "references_analyzed": 1,
                "binding_fingerprint": product_visual_binding_fingerprint(
                    product,
                    original_references,
                ),
            },
        },
    )

    assert profile["visual_context"]["status"] == "stale"
    assert "pet_owner" not in profile["themes"]
    assert not any(
        "dog owner park" in query
        for query in profile["search_clusters"]["direct"]
    )


def test_visual_reference_selection_prefers_detail_views_and_spreads_variants():
    references = [
        {
            "id": "front-base",
            "variant_id": None,
            "view_type": "front",
            "remote_file_id": "drive-1",
        },
        {
            "id": "front-red",
            "variant_id": "red",
            "view_type": "front",
            "remote_file_id": "drive-2",
        },
        {
            "id": "front-blue",
            "variant_id": "blue",
            "view_type": "front",
            "remote_file_id": "drive-3",
        },
        {
            "id": "logo",
            "variant_id": None,
            "view_type": "logo_closeup",
            "remote_file_id": "drive-4",
        },
        {
            "id": "embroidery",
            "variant_id": None,
            "view_type": "embroidery_closeup",
            "remote_file_id": "drive-5",
        },
    ]

    selected = select_product_visual_references(references, limit=4)

    assert [item["id"] for item in selected[:2]] == ["embroidery", "logo"]
    assert "front-base" in {item["id"] for item in selected}
    assert len({item["variant_id"] for item in selected}) >= 2


def test_visual_context_merge_uses_confidence_and_keeps_provenance():
    merged = merge_product_visual_context(
        [
            (
                ProductVisualContextDocument(
                    themes=["pet_owner"],
                    scene_hints=["dog owner park"],
                    audience_hints=["dog owner"],
                    occasion_hints=["pet gift"],
                    product_cues=["embroidered dog portrait"],
                    avoid_hints=[],
                    confidence=0.9,
                    summary="Visible dog portrait embroidery supports pet-owner context.",
                ),
                {"id": "r1", "view_type": "embroidery_closeup"},
                "gemini",
                "gemini-model",
            ),
            (
                ProductVisualContextDocument(
                    themes=["outdoor"],
                    scene_hints=["casual outdoor walk"],
                    audience_hints=[],
                    occasion_hints=[],
                    product_cues=["casual cap"],
                    avoid_hints=["formal studio"],
                    confidence=0.4,
                    summary="The cap construction suits casual outdoor use.",
                ),
                {"id": "r2", "view_type": "front"},
                "gemini",
                "gemini-model",
            ),
        ],
        binding_fingerprint="binding-1",
        analyzed_at="2026-09-30T00:00:00+00:00",
    )

    assert merged["status"] == "ready"
    assert merged["themes"][0] == "pet_owner"
    assert merged["references_analyzed"] == 2
    assert merged["reference_ids"] == ["r1", "r2"]
    assert merged["reference_views"] == ["embroidery_closeup", "front"]
    assert merged["binding_fingerprint"] == "binding-1"

def test_analysis_prompt_includes_ready_visual_product_context():
    product_context = {
        "name": "Custom dog portrait cap",
        "product_type": "cap",
        "discovery_context": {
            "themes": ["pet_owner"],
            "search_clusters": {
                "direct": ["dog owner park candid phone photo"],
            },
            "visual_context": {
                "status": "ready",
                "scene_hints": ["dog owner park"],
                "audience_hints": ["dog owner"],
                "occasion_hints": ["pet gift"],
                "product_cues": ["embroidered dog portrait"],
            },
        },
    }

    prompt = analysis_prompt(product_context)

    assert product_context_matching_active(product_context) is True
    assert "PRODUCT CONTEXT TARGETS" in prompt
    assert "pet_owner" in prompt
    assert "dog owner park" in prompt
    assert "context_match_score" in prompt


def test_stale_visual_context_is_not_used_for_candidate_context_matching():
    product_context = {
        "name": "Custom cap",
        "product_type": "cap",
        "discovery_context": {
            "themes": [],
            "search_clusters": {"direct": []},
            "visual_context": {
                "status": "stale",
                "scene_hints": ["dog owner park"],
                "audience_hints": ["dog owner"],
            },
        },
    }

    prompt = analysis_prompt(product_context)

    assert product_context_matching_active(product_context) is False
    assert "\n\nPRODUCT CONTEXT TARGETS (use only for context_match_score):" not in prompt
    assert "dog owner park" not in prompt


def test_context_feedback_learning_waits_for_consistent_evidence():
    one_mark = derive_context_feedback_learning([
        ("dog owner park candid phone photo", "good"),
    ])
    mixed = derive_context_feedback_learning([
        ("dog owner park candid phone photo", "good"),
        ("dog owner park candid phone photo", "wrong"),
    ])

    assert one_mark["active"] is False
    assert one_mark["promoted_queries"] == []
    assert mixed["active"] is False
    assert mixed["promoted_queries"] == []
    assert mixed["suppressed_queries"] == []


def test_context_feedback_learning_promotes_and_suppresses_consistent_queries():
    learned = derive_context_feedback_learning([
        ("dog owner park candid phone photo", "good"),
        ("dog owner park candid phone photo", "good"),
        ("studio fashion portrait", "wrong"),
        ("studio fashion portrait", "wrong"),
    ])

    assert learned["active"] is True
    assert learned["good_count"] == 2
    assert learned["wrong_count"] == 2
    assert learned["promoted_queries"] == [
        "dog owner park candid phone photo"
    ]
    assert learned["suppressed_queries"] == [
        "studio fashion portrait"
    ]


def test_product_context_profile_uses_learned_feedback_for_discovery():
    profile = derive_product_context_profile(
        product_snapshot={"name": "Custom dog portrait cap"},
        config={"auto_context": True},
        context_feedback=[
            ("dog owner park candid phone photo", "good"),
            ("dog owner park candid phone photo", "good"),
            ("woman casual lifestyle candid phone photo", "wrong"),
            ("woman casual lifestyle candid phone photo", "wrong"),
        ],
    )

    assert profile["feedback_learning"]["active"] is True
    assert profile["search_clusters"]["direct"][0] == (
        "dog owner park candid phone photo"
    )
    assert "woman casual lifestyle candid phone photo" not in (
        profile["search_clusters"]["generic"]
    )


def test_analysis_prompt_includes_human_learned_context_feedback():
    profile = derive_product_context_profile(
        product_snapshot={"name": "Custom dog portrait cap"},
        config={"auto_context": True},
        context_feedback=[
            ("dog owner park candid phone photo", "good"),
            ("dog owner park candid phone photo", "good"),
            ("studio fashion portrait", "wrong"),
            ("studio fashion portrait", "wrong"),
        ],
    )
    prompt = analysis_prompt({
        "name": "Custom dog portrait cap",
        "product_type": "cap",
        "discovery_context": profile,
    })

    assert "human-confirmed search contexts" in prompt
    assert "dog owner park candid phone photo" in prompt
    assert "human-rejected search contexts" in prompt
    assert "studio fashion portrait" in prompt


def test_context_feedback_ranking_keeps_base_score_without_active_learning():
    result = context_feedback_ranking_signal(
        profile={"feedback_learning": {"active": False}},
        source_query="dog owner park candid phone photo",
        base_score=0.72,
    )

    assert result["active"] is False
    assert result["adjustment"] == 0.0
    assert result["ranking_score"] == 0.72


def test_context_feedback_ranking_boosts_promoted_query():
    profile = derive_product_context_profile(
        product_snapshot={"name": "Custom dog portrait cap"},
        config={"auto_context": True},
        context_feedback=[
            ("dog owner park candid phone photo", "good"),
            ("dog owner park candid phone photo", "good"),
        ],
    )

    result = context_feedback_ranking_signal(
        profile=profile,
        source_query="dog owner park candid phone photo",
        base_score=0.72,
    )

    assert result["active"] is True
    assert result["direction"] == "boost"
    assert result["reviews"] == 2
    assert result["adjustment"] == 0.032
    assert result["ranking_score"] == 0.752


def test_context_feedback_ranking_downranks_suppressed_query():
    profile = derive_product_context_profile(
        product_snapshot={"name": "Custom dog portrait cap"},
        config={"auto_context": True},
        context_feedback=[
            ("studio fashion portrait", "wrong"),
            ("studio fashion portrait", "wrong"),
        ],
    )

    result = context_feedback_ranking_signal(
        profile=profile,
        source_query="studio fashion portrait",
        base_score=0.72,
    )

    assert result["active"] is True
    assert result["direction"] == "downrank"
    assert result["adjustment"] == -0.032
    assert result["ranking_score"] == 0.688


def test_context_feedback_ranking_ignores_unlearned_query():
    profile = derive_product_context_profile(
        product_snapshot={"name": "Custom dog portrait cap"},
        config={"auto_context": True},
        context_feedback=[
            ("dog owner park candid phone photo", "good"),
            ("dog owner park candid phone photo", "good"),
        ],
    )

    result = context_feedback_ranking_signal(
        profile=profile,
        source_query="family backyard candid phone photo",
        base_score=0.72,
    )

    assert result["active"] is False
    assert result["adjustment"] == 0.0
    assert result["ranking_score"] == 0.72


def test_context_feedback_ranking_adjustment_is_capped():
    profile = {
        "feedback_learning": {
            "active": True,
            "promoted_queries": ["dog owner park candid phone photo"],
            "suppressed_queries": [],
            "query_scores": [{
                "query": "dog owner park candid phone photo",
                "good": 100,
                "wrong": 0,
                "reviews": 100,
                "score": 1.0,
            }],
        },
    }

    result = context_feedback_ranking_signal(
        profile=profile,
        source_query="dog owner park candid phone photo",
        base_score=0.97,
    )

    assert result["adjustment"] == 0.06
    assert result["ranking_score"] == 1.0
