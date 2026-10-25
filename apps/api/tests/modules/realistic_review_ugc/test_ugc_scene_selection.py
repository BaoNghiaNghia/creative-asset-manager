from types import SimpleNamespace

from app.modules.realistic_review_ugc.ugc_scene_selection import (
    allowed_scout_image_url,
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
    ])
    assert [x.id for x in selected] == ["1", "3", "4"]


def test_excludes_unapproved_and_unsafe_candidates():
    selected = select_ugc_references([
        candidate(1, status="rejected"), candidate(2, image_url="http://localhost/a"),
        candidate(3), candidate(4), candidate(5, head_visible=False),
    ])
    assert selected == []


def test_requires_three_and_blocks_host_spoof():
    assert not allowed_scout_image_url("https://i.pinimg.com.evil.example/a.jpg")
    assert not allowed_scout_image_url("https://127.0.0.1/a.jpg")
    assert allowed_scout_image_url("https://i.pinimg.com/originals/a.jpg")
