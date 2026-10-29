"""Pure deterministic ROI dHash tests: no AI, storage or database I/O."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.typography_similarity import (
    candidate_matches, has_stale_roi_hash, is_typography_dhash,
    typography_dhash, typography_hamming_distance,
)


def image_bytes(*, reverse=False, background=(90, 90, 120), transparent=False):
    img = Image.new("RGBA", (160, 100), (0, 0, 0, 0) if transparent else (*background, 255))
    draw = ImageDraw.Draw(img)
    for i in range(0, 80):
        intensity = (255 - i * 3) if reverse else (15 + i * 3)
        draw.line((i, 0, i, 49), fill=(intensity, intensity, intensity, 255))
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


REGION = {"x": 0, "y": 0, "w": .5, "h": .5}


def test_dhash_only_sees_typography_roi_not_other_product_background():
    original = typography_dhash(image_bytes(background=(8, 9, 10)), REGION)
    other_background = typography_dhash(image_bytes(background=(240, 230, 200)), REGION)
    reverse = typography_dhash(image_bytes(reverse=True), REGION)
    assert is_typography_dhash(original)
    assert original == other_background
    assert typography_hamming_distance(original, other_background) == 0
    assert typography_hamming_distance(original, reverse) >= 50
    assert candidate_matches(original, other_background)
    assert not candidate_matches(original, reverse)


def test_dhash_is_deterministic_with_alpha_and_has_versioned_distance():
    value = typography_dhash(image_bytes(transparent=True), REGION)
    assert value == typography_dhash(image_bytes(transparent=True), REGION)
    assert len(value) == 21 and value.startswith("tdh1:")
    assert typography_hamming_distance(value, value) == 0
    assert typography_hamming_distance("tdh1:0000000000000000",
                                      "tdh1:ffffffffffffffff") == 64
    assert not is_typography_dhash("0123456789abcdef")
    assert not candidate_matches("tdh2:0000000000000000", value)
    for invalid in ("tdh2:0000000000000000", "tdh1:NO", "garbage"):
        with pytest.raises(ValueError, match="incompatible_typography_dhash"):
            typography_hamming_distance(invalid, value)


@pytest.mark.parametrize("bad_region", [
    {"x": .8, "y": 0, "w": .5, "h": .4},
    {"x": 0, "y": -.1, "w": .5, "h": .5},
    {"x": 0, "y": 0, "w": 0, "h": .5},
    {"x": 0, "y": 0, "w": float("nan"), "h": .5},
    {},
])
def test_dhash_rejects_invalid_region(bad_region):
    with pytest.raises(ValueError, match="invalid_typography_region"):
        typography_dhash(image_bytes(), bad_region)


def test_manual_bbox_edit_invalidates_old_fingerprint_without_provider_call():
    assert not has_stale_roi_hash(None)
    assert not has_stale_roi_hash({"override": None})
    assert not has_stale_roi_hash({"override": {"font_mood": {"primary": "serif"}}})
    assert has_stale_roi_hash({"override": {"typography_region": REGION}})
    assert has_stale_roi_hash("invalid-review-json")
    assert has_stale_roi_hash({"override": "invalid"})


def test_dhash_rejects_corrupt_images_and_invalid_thresholds():
    with pytest.raises(ValueError):
        typography_dhash(b"not-a-picture", REGION)
    with pytest.raises(ValueError):
        typography_dhash(b"x" * (12 * 1024 * 1024 + 1), REGION)
    with pytest.raises(ValueError, match="invalid_similarity_threshold"):
        candidate_matches("tdh1:0000000000000000", "tdh1:0000000000000000",
                          threshold=9)
