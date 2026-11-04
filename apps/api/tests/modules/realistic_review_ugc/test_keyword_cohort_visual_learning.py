from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.keyword_cohort_learning import (
    semantic_cohort, summarize_cross_keyword_feedback,
    cross_keyword_guidance,
)
from app.modules.realistic_review_ugc.stage1_visual_similarity import (
    stage1_visual_dhash, matches_rejected_visual,
    stage1_layout_signature, matches_sibling_layout,
)
from app.modules.realistic_review_ugc.typography_similarity import is_typography_dhash


def _metadata(decision, reason):
    return {"creative_feedback": {"decision": decision, "reason": reason}}


def test_cross_keyword_needs_three_distinct_other_quotes_and_six_votes():
    samples = [
        ("LOVE LIVE MUSIC", _metadata("rejected", "poor_cap_fit")),
        ("LOVE LIVE MUSIC", _metadata("rejected", "poor_cap_fit")),
        ("MUSIC IS LIFE", _metadata("rejected", "poor_cap_fit")),
        ("MUSIC IS LIFE", _metadata("rejected", "poor_cap_fit")),
        ("PROUD MUSICIAN", _metadata("rejected", "poor_cap_fit")),
        ("PROUD MUSICIAN", _metadata("rejected", "poor_cap_fit")),
    ]
    profile = summarize_cross_keyword_feedback(samples, keyword="MUSIC HEALS", min_keywords=3, min_votes=6)
    assert profile["eligible"] is True
    assert profile["distinct_keywords"] == 3
    assert profile["patterns"][0]["reason"] == "poor_cap_fit"
    assert "3 other phrases" in cross_keyword_guidance(profile)
    assert "Never" not in cross_keyword_guidance(profile)
    # 3 votes from one phrase must never establish a new global blacklist.
    insufficient = summarize_cross_keyword_feedback(samples[:2], keyword="MUSIC HEALS")
    assert insufficient["eligible"] is False
    assert insufficient["patterns"] == []
    # The current quote is excluded: self-feedback is already processed separately.
    identical = summarize_cross_keyword_feedback(samples, keyword="LOVE LIVE MUSIC")
    assert identical["eligible"] is False


def test_cross_keyword_is_topical_only_and_never_transfers_verbatim_phrase():
    samples = [
        ("MUSIC IS HERE", _metadata("rejected", "unreadable_text")),
        ("MUSIC AND LOVE", _metadata("rejected", "unreadable_text")),
        ("SONGS ARE LIFE", _metadata("rejected", "unreadable_text")),
        ("SONGS ARE LIFE", _metadata("picked", "unspecified")),
        ("MUSIC AND LOVE", _metadata("picked", "unspecified")),
        ("MUSIC IS HERE", _metadata("picked", "unspecified")),
        ("PET DOG", _metadata("rejected", "wrong_spelling")),
    ]
    profile = summarize_cross_keyword_feedback(samples, keyword="MUSIC IS BEAUTIFUL")
    assert profile["eligible"]
    assert profile["vote_count"] == 6
    assert len(profile["patterns"]) == 1
    assert profile["patterns"][0]["reason"] == "unreadable_text"
    guidance = cross_keyword_guidance(profile)
    assert "MUSIC AND LOVE" not in guidance
    assert "PET DOG" not in guidance
    assert semantic_cohort("BORN TO BE A COWBOY") == "western"
    assert semantic_cohort("JUST A QUOTE") is None


def png_design(*, shift=0):
    image = Image.new("RGBA", (200, 100), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40 + shift, 20, 160 + shift, 28), fill=(5, 5, 5, 255))
    draw.rectangle((52 + shift, 43, 143 + shift, 56), fill=(5, 5, 5, 255))
    draw.rectangle((60 + shift, 73, 130 + shift, 84), fill=(5, 5, 5, 255))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_stage1_visual_hash_normalizes_transparent_canvas_and_blocks_near_rejected():
    fingerprint = stage1_visual_dhash(png_design())
    moved = stage1_visual_dhash(png_design(shift=5))
    assert is_typography_dhash(fingerprint)
    assert moved == fingerprint
    assert matches_rejected_visual(moved, [fingerprint])
    assert not matches_rejected_visual("invalid", [fingerprint])
    assert not matches_rejected_visual(moved, ["garbage"])
    try:
        stage1_visual_dhash(b"not a PNG")
    except ValueError:
        pass
    else:
        raise AssertionError("Corrupted images must fail validation")



def test_h2_sibling_layout_guard_is_color_invariant_and_conservative():
    first = png_design()
    with Image.open(BytesIO(first)) as original:
        alpha = original.getchannel("A")
    recolored = Image.new("RGBA", (200, 100), (242, 13, 15, 0))
    recolored.putalpha(alpha)
    stream = BytesIO()
    recolored.save(stream, "PNG")
    different_ink = stream.getvalue()
    signature = stage1_layout_signature(first)
    assert signature.startswith("als1:")
    assert signature == stage1_layout_signature(different_ink)
    assert matches_sibling_layout(stage1_layout_signature(different_ink), [signature])
    assert not matches_sibling_layout("garbage", [signature])
    assert not matches_sibling_layout(signature, ["invalid", "als1:no"])
    alternative = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(alternative)
    draw.ellipse((45, 15, 160, 90), fill=(0, 0, 0, 255))
    stream = BytesIO()
    alternative.save(stream, "PNG")
    assert not matches_sibling_layout(stage1_layout_signature(stream.getvalue()), [signature])


def test_v3_checked_png_exposes_visual_signature_for_output_history(monkeypatch):
    from app.modules.realistic_review_ugc import creative_v3_runner
    from app.providers.ai.codex_image import CodexImageProviderError
    monkeypatch.setattr(creative_v3_runner, "inspect_typography_png", lambda raw: {
        "technical_pass": True, "alpha_bbox": [1, 2, 190, 95],
    })
    from hashlib import sha256
    raw = png_design()
    digest, qa = creative_v3_runner._checked_png(raw, set())
    assert digest == sha256(raw).hexdigest()
    assert qa["stage1_visual_dhash"] == stage1_visual_dhash(raw)
    assert qa["technical_pass"]
    try:
        creative_v3_runner._checked_png(raw, {digest})
    except CodexImageProviderError as error:
        assert error.code == "creative_v3_duplicate_image"
    else:
        raise AssertionError("Exact duplicate should remain rejected")


def test_failed_sample_does_not_change_global_policy():
    review = summarize_cross_keyword_feedback(
        [("DOG MAMA", _metadata("rejected", "wrong_style"))], keyword="CAT MAMA"
    )
    assert review["eligible"] is False
    assert cross_keyword_guidance(review) == ""
