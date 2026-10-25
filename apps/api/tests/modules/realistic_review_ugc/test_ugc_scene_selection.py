from types import SimpleNamespace

from app.modules.realistic_review_ugc.ugc_scene_selection import (
    allowed_scout_image_url,
    eligible_ugc_references,
    select_ugc_references,
)


def candidate(number, signature=None, **overrides):
    values = dict(
        id=str(number), status="approved",
        image_url=f"https://i.pinimg.com/originals/{number}.jpg",
        content_hash=f"hash-{number}", head_visible=True, people_count=1,
        mobile_ugc_score=0.9, ai_signal_json={},
        final_score=100-number, diversity_signature=signature,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_picks_three_distinct_diverse_approved_scout_images():
    selected = select_ugc_references([
        candidate(1, "same"), candidate(2, "same"),
        candidate(3, "different"), candidate(4, "third"),
    ], selection_key="design-one")
    assert len(selected) == 3
    assert len({item.id for item in selected}) == 3
    assert len({item.diversity_signature for item in selected}) == 3


def test_samples_different_designs_without_top_three_bias_and_stable_for_retries():
    pool = [candidate(n, f"scene-{n}") for n in range(1, 21)]
    first = [c.id for c in select_ugc_references(pool, selection_key="tenant:design-one:rev1")]
    second = [c.id for c in select_ugc_references(pool, selection_key="tenant:design-two:rev1")]
    assert len(set(first)) == len(set(second)) == 3
    assert first != ["1", "2", "3"]
    assert first != second
    assert first == [c.id for c in select_ugc_references(
        list(reversed(pool)), selection_key="tenant:design-one:rev1",
    )]
    assert first == [c.id for c in select_ugc_references(
        pool, selection_key="tenant:design-one:rev1",
    )]


def test_good_feedback_prioritized_and_bad_ai_never_selected():
    pool = [candidate(n, f"scene-{n}") for n in range(1, 21)]
    pool[6].ai_signal_json = {"reference_manual_label": "good"}
    pool[0].ai_signal_json = {"reference_manual_label": "bad"}
    pool[1].ai_signal_json = {"reference_manual_label": "ai"}
    ids = [item.id for item in select_ugc_references(pool, selection_key="design-1")]
    assert len(ids) == 3
    assert "7" in ids
    assert "1" not in ids and "2" not in ids
    assert "7" in [item.id for item in eligible_ugc_references(pool)]
    assert "1" not in [item.id for item in eligible_ugc_references(pool)]


def test_selects_from_same_first_twenty_visible_scout_gallery_items():
    pool = [candidate(n, f"scene-{n}") for n in range(1, 31)]
    visible = eligible_ugc_references(pool)
    assert len(visible) == 20
    assert [i.id for i in visible] == [str(n) for n in range(1, 21)]
    assert {item.id for item in select_ugc_references(pool, selection_key="design-1")}.issubset(
        {item.id for item in visible}
    )


def test_excludes_unapproved_and_unsafe_candidates():
    selected = select_ugc_references([
        candidate(1, status="rejected"), candidate(2, image_url="http://localhost/a"),
        candidate(3), candidate(4), candidate(5, head_visible=False),
    ], selection_key="design-1")
    assert selected == []


def test_requires_three_and_blocks_host_spoof():
    assert not allowed_scout_image_url("https://i.pinimg.com.evil.example/a.jpg")
    assert not allowed_scout_image_url("https://127.0.0.1/a.jpg")
    assert allowed_scout_image_url("https://i.pinimg.com/originals/a.jpg")
