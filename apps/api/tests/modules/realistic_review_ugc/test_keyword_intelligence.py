from hashlib import sha256
from uuid import uuid4

from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION
from app.modules.realistic_review_ugc.keyword_intelligence import build_keyword_intelligence
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)


def word(text="THIS IS MY COWBOY HAT", *, volume=0, provider="pending"):
    return RrugcKeywordVolumeModel(
        id=str(uuid4()), tenant_id="tenant-a",
        keyword=text, keyword_normalized=text.casefold(),
        search_volume=volume, provider=provider,
    )


def brief_for(row, *, status="completed", version=PROMPT_VERSION):
    concepts = []
    for mode, count in (("safe", 1), ("creative_twist", 4), ("explore", 4)):
        concepts.append({
            "mode": mode, "name": f"{mode} idea", "familiar_format": "Western",
            "creative_hook": mode + " lettering", "creative_twist": "new lettering",
            "design_variants": [
                f"Unique {mode} arrangement {index} for this phrase" for index in range(count)
            ],
        })
    return RrugcKeywordCreativeBriefModel(
        id=str(uuid4()), tenant_id=row.tenant_id, keyword_id=row.id,
        keyword_sha256=sha256(row.keyword.encode()).hexdigest(),
        prompt_version=version, status=status,
        brief_json={
            "keyword_exact": row.keyword, "target_audience": "Fans of cowboy humor",
            "purchase_motivation": "Personal identity and gift humor",
            "meaning_emotion": "Playful self-expression",
            "strategy": "Meaning-first", "creative_hook": "Playful western reference",
            "concepts": concepts,
        },
    )


def test_unanalyzed_keyword_never_invents_buyer_or_originality_score():
    result = build_keyword_intelligence(word(), None)
    assert result["brief_status"] == "not_analyzed"
    assert result["buyer"]["audience"] is None
    assert result["creative"]["direction_count"] == 0
    assert result["score"]["value"] is None
    assert result["score"]["coverage"] == 20
    components = {entry["key"]: entry for entry in result["score"]["dimensions"]}
    assert components["buyer"]["score"] is None
    assert components["creative"]["score"] is None
    assert components["market"]["score"] is None
    assert components["originality"]["score"] is None
    assert result["evidence"]["volume"] is None


def test_ready_creative_brief_supplies_hypotheses_not_commercial_proof():
    row = word(volume=25, provider="aebrowse_google_ads")
    report = build_keyword_intelligence(row, brief_for(row))
    assert report["score"]["coverage"] == 90
    assert report["score"]["value"] is not None
    assert report["score"]["is_preliminary"] is True
    assert report["buyer"]["source"] == "ai_hypothesis"
    assert report["buyer"]["audience"] == "Fans of cowboy humor"
    assert report["creative"]["direction_count"] == 9
    assert report["creative"]["originality_checked"] is False
    assert report["score"]["confidence"] == "medium"
    assert report["evidence"]["trademark_legal_clearance"] is False


def test_stale_brief_cannot_trick_scoring_or_rewrite_the_keyword():
    row = word("PRESERVE THE EXACT QUOTE", volume=12, provider="aebrowse_google_ads")
    brief = brief_for(row, version="meaning-first-144-v1")
    report = build_keyword_intelligence(row, brief)
    assert report["keyword_exact"] == "PRESERVE THE EXACT QUOTE"
    assert report["brief_status"] == "stale"
    assert report["buyer"]["audience"] is None
    assert report["score"]["coverage"] == 40
    assert report["score"]["value"] is None


def test_zero_volume_still_allows_exploration_with_analyzed_brief():
    row = word(volume=0, provider="aebrowse_google_ads")
    result = build_keyword_intelligence(row, brief_for(row))
    assert result["evidence"]["volume"] == 0
    assert result["score"]["recommendation"] in {"explore", "prioritize"}
    assert result["score"]["coverage"] == 90


def test_long_phrase_warns_about_cap_fit_and_keeps_full_phrase():
    phrase = "IDK WHATS GOING ON BUT ITS A LOT AND THERE IS STILL MORE TO SAY ON THIS CAP"
    report = build_keyword_intelligence(word(phrase), None)
    assert report["keyword_exact"] == phrase
    assert report["embroidery"]["word_count"] >= 12
    assert report["embroidery"]["preflight_score"] < 40
    assert len(report["embroidery"]["warnings"]) >= 2


def test_trademark_flag_is_independent_of_numeric_opportunity_score():
    row = word(volume=500, provider="aebrowse_google_ads")
    row.trademark_source = "aebrowse_google_ads"
    row.trademark_status = "danger"
    row.provider_raw_json = {
        "trademark": {"status": "DANGER"},
        "_trademark_screened_keyword": row.keyword,
    }
    report = build_keyword_intelligence(row, brief_for(row))
    assert report["score"]["recommendation"] == "hold"
    assert report["evidence"]["trademark_status"] == "danger"


def test_existing_feedback_is_scoped_and_explained_without_auto_rejection():
    row = word(volume=100, provider="aebrowse_google_ads")
    feedback = {
        "version": 1, "total_votes": 3,
        "picked": [{"mode": "safe", "variant": "A", "count": 1}],
        "rejected": [{"mode": "explore", "variant": "B", "count": 2,
                      "reason": "poor_cap_fit", "guidance": "Improve fit"}],
        "general_rejections": [], "rejected_hashes": [],
    }
    report = build_keyword_intelligence(row, brief_for(row), feedback)
    assert report["learning"]["picked_votes"] == 1
    assert report["learning"]["rejected_votes"] == 2
    assert report["learning"]["scope"] == "tenant_and_exact_keyword"
    assert report["score"]["value"] is not None
