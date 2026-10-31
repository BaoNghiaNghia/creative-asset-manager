"""Feedback-memory regression checks: never block all cap stacks by title."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cap_stack_feedback import (
    CapStackFeedbackFilter, SmallFingerprint, _url_keys,
    caption_is_near_duplicate, distinctive_words, visually_near_duplicate,
)


def candidate(pin: int, image: str, text: str = ""):
    return SimpleNamespace(
        pin_url=f"https://www.pinterest.com/pin/{pin}/",
        image_url=f"https://i.pinimg.com/736x/{image}.jpg",
        alt_text=text,
    )


def task():
    return {
        "scout_type": "cap_stack",
        "related_seeds": [
            {"pin_url": "https://www.pinterest.com/pin/101/",
             "image_url": "https://i.pinimg.com/originals/yes.jpg",
             "alt_text": "vintage retro denim sports cap stack with embroidery"},
        ],
        "rejected_seeds": [
            {"pin_url": "https://www.pinterest.com/pin/111/",
             "image_url": "https://i.pinimg.com/236x/rejected.jpg",
             "alt_text": "low quality brand advertising infographic poster layout",
             "label": "bad"},
            {"pin_url": "https://www.pinterest.com/pin/222/",
             "image_url": "https://i.pinimg.com/736x/ai-illustration.jpg",
             "alt_text": "artificial fake cartoon baseball caps arrangement",
             "label": "ai"},
        ],
    }


def test_rejected_pin_id_and_image_asset_renditions_are_filtered():
    filt = CapStackFeedbackFilter(task())
    assert filt.quickly_rejected(candidate(111, "unrelated"))
    assert filt.quickly_rejected(candidate(333, "rejected"))
    assert filt.quickly_rejected(candidate(444, "ai-illustration"))
    assert _url_keys("https://i.pinimg.com/236x/image/a.jpg")[1] == _url_keys(
        "https://i.pinimg.com/originals/image/a.jpg")[1]
    assert not filt.quickly_rejected(candidate(555, "new-image", "authentic vintage baseball caps stacked"))


def test_generic_cap_stack_titles_cannot_blacklist_all_photos():
    # Rejecting one generic title must not ban every legitimate Cap Stack.
    filt = CapStackFeedbackFilter({
        "rejected_seeds": [
            {"pin_url": "https://www.pinterest.com/pin/111/",
             "image_url": "https://i.pinimg.com/236x/111.jpg",
             "alt_text": "Baseball cap stack pile of trucker hats"},
        ]
    })
    assert not filt.quickly_rejected(candidate(222, "another", "Baseball cap stack pile of trucker hats"))
    assert not caption_is_near_duplicate(distinctive_words("Cap Stack"),
                                         distinctive_words("Baseball Cap Stack"))


def test_distinctive_rejected_captions_and_good_override():
    filt = CapStackFeedbackFilter(task())
    assert filt.quickly_rejected(candidate(
        999, "new", "low quality advertising infographic poster layout brand"))
    assert not filt.quickly_rejected(candidate(
        999, "new", "vintage retro denim sports cap stack with embroidery"))


def test_visual_match_requires_both_near_identical_structure_and_color():
    rejected = SmallFingerprint(dhash=0x1234567890ABCDEF, rgb=(100, 130, 145))
    assert visually_near_duplicate(
        rejected, SmallFingerprint(dhash=0x1234567890ABCDEE, rgb=(102, 133, 148))
    )
    assert not visually_near_duplicate(
        rejected, SmallFingerprint(dhash=0xEEEEEEEEEEEEEEEE, rgb=(100, 130, 145))
    )
    assert not visually_near_duplicate(
        rejected, SmallFingerprint(dhash=0x1234567890ABCDEE, rgb=(220, 20, 20))
    )


@pytest.mark.asyncio
async def test_resolved_candidate_visual_near_duplicate_is_excluded(monkeypatch):
    filt = CapStackFeedbackFilter(task())
    filt.hashes = [SmallFingerprint(0x1111222233334444, (95, 120, 150))]
    async def fake_hash(_client, url):
        if "new-duplicate" in url:
            return SmallFingerprint(0x1111222233334445, (95, 120, 150))
        return SmallFingerprint(0xE00F0F0FF0F0F0F0, (240, 20, 40))
    monkeypatch.setattr(filt, "_get_hash", fake_hash)
    rows = [candidate(333, "new-duplicate"), candidate(334, "new-good")]
    keep, excluded = await filt.filter_resolved(rows)
    assert [row.pin_url for row in keep] == [rows[1].pin_url]
    assert [row.pin_url for row in excluded] == [rows[0].pin_url]


@pytest.mark.asyncio
async def test_no_visual_library_gracefully_keeps_non_rejected(monkeypatch):
    filt = CapStackFeedbackFilter(task())
    filt.hashes = []
    keep, excluded = await filt.filter_resolved([
        candidate(443, "rejected"), candidate(444, "new-good"),
    ])
    assert len(keep) == 1
    assert len(excluded) == 1
