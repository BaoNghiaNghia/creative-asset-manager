from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.creative_brief import build_prompt
from app.modules.realistic_review_ugc.creative_v3_generation import (
    PROMPT_VERSION, _brief_plan, prompt_for_slot,
)
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)
from app.modules.realistic_review_ugc.stage1_design_learning import (
    learning_fingerprint, learning_profile, learning_prompt_context,
    set_design_feedback,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordImageJobModel.__table__.create(engine)
    RrugcImageOutputVersionModel.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def design(session, *, tenant="tenant-a", quote="THIS IS MY COWBOY HAT",
           mode="creative_twist", variant="B", digest="a" * 64):
    word = RrugcKeywordVolumeModel(
        id=str(uuid4()), tenant_id=tenant, keyword=quote,
        keyword_normalized=quote.lower(), picked=True, search_volume=2,
    )
    job = RrugcKeywordImageJobModel(
        id=str(uuid4()), tenant_id=tenant, keyword_id=word.id,
        keyword_text=quote, skill_source="local", skill_name="creative-brief-v3",
        prompt_text="test", created_by_user_id="actor",
    )
    row = RrugcImageOutputVersionModel(
        id=str(uuid4()), tenant_id=tenant, stage="stage1", job_id=job.id,
        version=1, remote_file_id=str(uuid4()), content_type="image/png",
        font_info_json={
            "engine": "creative-v3", "concept_mode": mode,
            "concept_variant": variant, "content_sha256": digest,
        },
    )
    session.add_all([word, job, row])
    session.commit()
    return row


def test_picked_rejected_and_restore_are_durable_without_erasing_image_metadata(session):
    row = design(session)
    before_file = row.remote_file_id
    assert set_design_feedback(row, decision="picked", reason="duplicate", actor_id="owner")["reason"] == "unspecified"
    session.commit()
    assert learning_profile(session, tenant_id="tenant-a", keyword="THIS IS MY COWBOY HAT")["picked"][0]["count"] == 1
    set_design_feedback(row, decision="rejected", reason="poor_cap_fit", actor_id="owner")
    session.commit()
    session.expire_all()
    profile = learning_profile(session, tenant_id="tenant-a", keyword="THIS IS MY COWBOY HAT")
    assert profile["rejected"][0]["reason"] == "poor_cap_fit"
    assert profile["rejected_hashes"] == ["a" * 64]
    assert "curved cap front" in learning_prompt_context(profile)
    assert row.remote_file_id == before_file
    assert row.font_info_json["engine"] == "creative-v3"
    set_design_feedback(row, decision="neutral", reason="unspecified", actor_id="owner")
    session.commit()
    assert learning_profile(session, tenant_id="tenant-a", keyword="THIS IS MY COWBOY HAT")["total_votes"] == 0


def test_feedback_is_strictly_tenant_and_quote_scoped(session):
    a = design(session, digest="a" * 64)
    b = design(session, tenant="tenant-b", digest="b" * 64)
    c = design(session, quote="DIFFERENT HAT", digest="c" * 64)
    for row in (a, b, c):
        set_design_feedback(row, decision="rejected", reason="duplicate", actor_id="owner")
    session.commit()
    profile = learning_profile(session, tenant_id="tenant-a", keyword="THIS IS MY COWBOY HAT")
    assert profile["total_votes"] == 1
    assert profile["rejected_hashes"] == ["a" * 64]


def test_feedback_invalid_decision_rejected_without_side_effects(session):
    row = design(session)
    original = row.font_info_json
    with pytest.raises(ValueError):
        set_design_feedback(row, decision="nonsense", reason="duplicate", actor_id="owner")
    assert row.font_info_json == original
    with pytest.raises(ValueError):
        set_design_feedback(row, decision="rejected", reason="bad", actor_id="owner")


def test_rejected_slot_is_replanned_with_original_quote_intact():
    phrase = "THIS IS MY COWBOY HAT"
    variants = []
    for mode, count in (("safe", 1), ("creative_twist", 4), ("explore", 4)):
        variants.append({
            "mode": mode, "name": "Concept " + mode, "familiar_format": "Western",
            "creative_twist": "letter remix", "description": "Typography design",
            "creative_hook": "Western " + mode, "typography_plan": "Clear typography",
            "embroidery_plan": "Legible", "clarity_assessment": "Unverified",
            "embroidery_fit": "Unverified", "risks": [],
            "design_variants": [
                f"{mode} cap lettering route {i}: original compact typographic geometry"
                for i in range(count)
            ],
        })
    plan = _brief_plan(
        keyword=phrase, brief_json={
            "keyword_exact": phrase, "strategy": "identity_first",
            "meaning_emotion": "Rodeo fun", "target_audience": "Cowboys",
            "purchase_motivation": "identity", "creative_hook": "Western humor",
            "concepts": variants,
        },
        prompt_version=PROMPT_VERSION, brief_id="brief-a",
    )
    default = prompt_for_slot(plan, 3)
    profile = {
        "rejected": [{
            "mode": "creative_twist", "variant": "B",
            "count": 1, "votes": 1, "reason": "duplicate",
            "guidance": "Change the primary word silhouette, hierarchy and layout substantially.",
            "strong_negative": True,
        }],
        "picked": [], "rejected_hashes": ["a" * 64], "total_votes": 1,
    }
    plan["learning_profile"] = profile
    plan["learning_context"] = learning_prompt_context(profile)
    changed = prompt_for_slot(plan, 3)
    assert "REPLACEMENT CONCEPT" in changed
    assert "HUMAN REJECTION LEARNING" in changed
    assert "REPLACEMENT CONCEPT" not in default
    assert phrase in changed
    assert learning_fingerprint(profile) == learning_fingerprint(dict(profile))
    assert "REJECTED" in build_prompt(phrase, learning_context=plan["learning_context"])
