"""Creative Engine V3 P1: read-only, tenant-scoped V1 bridge regression."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.creative_engine_v3 import (
    SCHEMA_VERSION, build_v3_preview,
)
from app.modules.realistic_review_ugc.creative_brief_service import get_v3_preview, v3_enabled
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)

EXAMPLE_QUOTES = (
    "SUPPORT LIVE MUSIC HIRE LIVE MUSICIANS",
    "IDK WHAT'S GOING ON BUT IT'S A LOT",
    "I CRY A LOT BUT I AM SO PRODUCTIVE",
    "CREATIVE DICTATOR",
    "KEEP ON TRUCKIN'",
    "BAD DAY TO BE A HOTDOG",
    "I DON'T HAVE A PLAN, I HAVE COFFEE",
    "SOME DAYS I AM THE STORM",
    "MORE BOOKS, LESS DRAMA",
    "MY DOG IS MY PERSONAL TRAINER",
    "I AM HERE FOR THE LIVE MUSIC",
    "NOT TODAY, MONDAY",
    "BORN TO CREATE, FORCED TO EMAIL",
    "STAY CURIOUS",
    "TAKE THE SCENIC ROUTE",
    "THINK BIG, STITCH SMALL",
    "DO LESS, FEEL MORE!",
)


def brief(keyword: str) -> dict:
    return {
        "keyword_exact": keyword, "strategy": "humor_first",
        "meaning_emotion": "Human humor and identity",
        "target_audience": "People who enjoy witty quotes",
        "purchase_motivation": "Self-expression",
        "creative_hook": "Meaningful visual arrangement",
        "concepts": [
            {
                "name": mode + " typography", "mode": mode,
                "familiar_format": "quote-first typographic badge",
                "creative_twist": "one contrast-based text interaction",
                "description": "An original layout for the exact words",
                "creative_hook": "hook " + mode,
                "typography_plan": "readable sans letters",
                "embroidery_plan": "flat satin",
                "clarity_assessment": "requires visual check",
                "embroidery_fit": "requires sample",
                "risks": [],
            } for mode in ("safe", "creative_twist", "explore")
        ],
    }


@pytest.mark.parametrize("keyword", EXAMPLE_QUOTES)
def test_quote_locked_with_three_stable_distinct_concepts(keyword):
    item = build_v3_preview(
        keyword=keyword, brief_json=brief(keyword), source_prompt_version="meaning-first-v1",
    )
    assert item["schema_version"] == SCHEMA_VERSION
    assert item["keyword_exact"] == keyword
    assert item["exact_text_locked"] is True
    assert item["planner_status"] == "v1_bridge_preview_only"
    assert item["intent_hint"] == "humor_first"
    assert [x["source_mode"] for x in item["concepts"]] == ["safe", "creative_twist", "explore"]
    assert len({x["id"] for x in item["concepts"]}) == 3
    assert all(x["text_spans"] == [{"text": keyword, "role": "locked_quote"}]
               for x in item["concepts"])
    assert all(x["design_blueprint_status"] == "not_composed" for x in item["concepts"])
    assert all(x["quality"]["status"] == "needs_review" for x in item["concepts"])
    assert item["concepts"][1]["mechanism"] is None
    assert item["concepts"][2]["mechanism"] is None
    assert not item["can_generate_artwork"]
    assert not item["job_created"]
    assert not item["stage1_output_saved"]
    assert not item["stage2_handoff_performed"]
    assert not item["imagegen_calls"]
    repeat = build_v3_preview(
        keyword=keyword, brief_json=brief(keyword), source_prompt_version="meaning-first-v1",
    )
    assert repeat == item


def test_rejects_changed_keyword_without_normalizing_case_spacing_or_punctuation():
    raw = brief("I  LOVE MY DOG!")
    with pytest.raises(ValueError, match="keyword text changed"):
        build_v3_preview(keyword="I LOVE MY DOG!", brief_json=raw,
                         source_prompt_version="meaning-first-v1")


def test_fingerprint_changes_with_content_without_changing_original_text():
    source = brief("STAY CURIOUS")
    first = build_v3_preview(keyword="STAY CURIOUS", brief_json=source,
                             source_prompt_version="meaning-first-v1")
    source["concepts"][1]["creative_hook"] = "a different original visual hook"
    second = build_v3_preview(keyword="STAY CURIOUS", brief_json=source,
                              source_prompt_version="meaning-first-v1")
    assert first["keyword_sha256"] == second["keyword_sha256"]
    assert first["concepts"][1]["id"] != second["concepts"][1]["id"]
    assert first["source"]["brief_content_sha256"] != second["source"]["brief_content_sha256"]


def test_unknown_intent_is_not_guessed_from_narrative():
    source = brief("MY FAVORITE SONG")
    source["strategy"] = "a poetic description of a concert"
    plan = build_v3_preview(keyword="MY FAVORITE SONG", brief_json=source,
                            source_prompt_version="meaning-first-v1")
    assert plan["intent_hint"] == "unclassified"


def test_long_quote_and_unsupported_controls_cannot_qualify_for_svg_preview():
    for keyword in ("A " * 110, "SAY\nHELLO"):
        plan = build_v3_preview(keyword=keyword, brief_json=brief(keyword),
                                source_prompt_version="meaning-first-v1")
        assert all("exact_text_svg_preview_not_eligible" in c["quality"]["reasons"]
                   for c in plan["concepts"])
        assert plan["keyword_exact"] == keyword


def test_exploit_does_not_claim_to_be_ready_without_approved_feedback():
    plan = build_v3_preview(keyword="KEEP IT SIMPLE", brief_json=brief("KEEP IT SIMPLE"),
                            source_prompt_version="meaning-first-v1")
    assert plan["mode_policy"]["exploit"] == "requires_explicit_approved_feedback"
    assert all(c["mechanism_verified"] is False for c in plan["concepts"])


@pytest.mark.parametrize("enabled,allow,tenant,expected", (
    (False, "a", "a", False),
    (True, "", "a", False),
    (True, "a,b", "b", True),
    (True, "a,b", "z", False),
    (True, "tenant-a ", "tenant-a", True),
    (True, "a, b", "", False),
))
def test_v3_feature_gate_is_tenant_scoped(enabled, allow, tenant, expected):
    config = SimpleNamespace(CREATIVE_ENGINE_V3_ENABLED=enabled,
                             CREATIVE_ENGINE_V3_CANARY_TENANT_IDS=allow)
    assert v3_enabled(config, tenant) is expected


def test_v3_service_cannot_read_other_tenant_and_never_changes_v1_row():
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            keyword = "SUPPORT LIVE MUSIC"
            row = RrugcKeywordVolumeModel(
                id=str(uuid4()), tenant_id="tenant-a", keyword=keyword,
                keyword_normalized=keyword.lower(), picked=True, search_volume=20,
            )
            session.add(row)
            session.flush()
            stored = RrugcKeywordCreativeBriefModel(
                id=str(uuid4()), tenant_id="tenant-a", keyword_id=row.id,
                keyword_sha256=__import__("hashlib").sha256(keyword.encode()).hexdigest(),
                prompt_version="meaning-first-v1", status="completed",
                brief_json=brief(keyword),
            )
            session.add(stored)
            session.commit()
            with pytest.raises(LookupError):
                get_v3_preview(session, tenant_id="tenant-b", keyword_id=row.id)
            first = get_v3_preview(session, tenant_id="tenant-a", keyword_id=row.id)
            second = get_v3_preview(session, tenant_id="tenant-a", keyword_id=row.id)
            assert first == second
            # Old meaning-first-v1 briefs are intentionally stale after the
            # 1/4/4 prompt-version upgrade; this read-only endpoint never
            # modifies the stored brief or leaks another tenant's data.
            assert first["source_status"] == "stale"
            assert first["planner_status"] == "v1_brief_not_ready"
            session.refresh(stored)
            assert stored.status == "completed"
            assert stored.brief_json == brief(keyword)
            assert session.scalar(select(RrugcKeywordCreativeBriefModel).where(
                RrugcKeywordCreativeBriefModel.id == stored.id)) is stored
    finally:
        engine.dispose()


def test_v3_service_does_not_fabricate_when_v1_not_ready():
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            row = RrugcKeywordVolumeModel(
                id=str(uuid4()), tenant_id="t", keyword="WAIT FOR IT",
                keyword_normalized="wait for it", picked=True, search_volume=0,
            )
            session.add(row)
            session.commit()
            plan = get_v3_preview(session, tenant_id="t", keyword_id=row.id)
            assert plan["planner_status"] == "v1_brief_not_ready"
            assert plan["source_status"] == "not_analyzed"
            assert plan["concepts"] == []
            assert not plan["can_generate_artwork"]
    finally:
        engine.dispose()

def test_v3_status_route_is_not_shadowed_by_keyword_detail():
    from app.modules.realistic_review_ugc.creative_brief_router import router
    paths = [route.path for route in router.routes if hasattr(route, "path")]
    assert paths.index("/keyword-creative-briefs/v3/status") < paths.index(
        "/keyword-creative-briefs/{keyword_id}"
    )


def test_disabled_v3_data_route_fails_closed_before_db_access(monkeypatch):
    from fastapi import HTTPException
    from app.modules.realistic_review_ugc import creative_brief_router
    monkeypatch.setattr(creative_brief_router, "get_settings", lambda: SimpleNamespace(
        CREATIVE_ENGINE_V3_ENABLED=False,
        CREATIVE_ENGINE_V3_CANARY_TENANT_IDS="tenant-a",
    ))
    principal = SimpleNamespace(active_tenant_id="tenant-a")
    availability = creative_brief_router.creative_engine_v3_status(principal=principal)
    assert availability["available"] is False
    with pytest.raises(HTTPException) as failure:
        creative_brief_router.creative_engine_v3_preview(
            keyword_id="any", session=None, principal=principal,
        )
    assert failure.value.status_code == 404
