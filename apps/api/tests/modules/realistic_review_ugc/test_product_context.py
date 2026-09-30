from app.modules.realistic_review_ugc.keyword_strategy import build_campaign_search_queries
from app.modules.realistic_review_ugc.product_context import (
    derive_product_context_profile,
)
from app.modules.realistic_review_ugc.scout_automation import adaptive_search_queries


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
