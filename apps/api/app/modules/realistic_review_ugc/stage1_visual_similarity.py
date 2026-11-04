"""Stage 1 near-duplicate guard based on typography-only 64-bit perceptual hash.

Compares ONLY new generated artwork for the same tenant/phrase against images
explicitly rejected by a human. A threshold of 2 is conservative; the
fingerprint is not proof of identity, geometry exactness, or spelling accuracy.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.modules.realistic_review_ugc.typography_similarity import (
    candidate_matches, is_typography_dhash,
)
from app.modules.realistic_review_ugc.typography_quality_gate import (
    MAX_PNG_BYTES, _rgba_dhash,
)

SIMILARITY_DISTANCE = 2
# Only H2 fresh generation applies this second, color-invariant silhouette guard.
# A conservative distance rejects near-identical geometries without blocking
# ordinary shared vocabulary, fonts, or similar cap-safe bounding boxes.
SIBLING_LAYOUT_DISTANCE = 8


def stage1_layout_signature(raw_png: bytes) -> str:
    """Compact alpha silhouette; ignores new thread colors and transparent margin.

    This is a heuristic for duplicate composition, NOT an OCR or originality
    certificate. Decode only once on fresh H2 output and do not store pixels.
    """
    if not isinstance(raw_png, bytes) or not 0 < len(raw_png) <= MAX_PNG_BYTES:
        raise ValueError("invalid_stage1_visual_source")
    try:
        with Image.open(BytesIO(raw_png)) as image:
            if image.format != "PNG" or image.width < 8 or image.height < 8:
                raise ValueError("invalid_stage1_visual_source")
            alpha = image.convert("RGBA").getchannel("A")
            bounds = alpha.getbbox()
            if not bounds:
                raise ValueError("empty_stage1_artwork")
            tile = alpha.crop(bounds).resize(
                (16, 16), resample=Image.Resampling.LANCZOS,
            )
            bits = 0
            for value in tile.getdata():
                bits = (bits << 1) | int(value >= 48)
            return f"als1:{bits:064x}"
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid_stage1_visual_source") from exc


def matches_sibling_layout(candidate: str, previous: list[str] | tuple[str, ...]) -> bool:
    """Only practically identical alpha layouts; not a general style classifier."""
    if not isinstance(candidate, str) or not candidate.startswith("als1:"):
        return False
    try:
        if len(candidate) != 69:
            return False
        number = int(candidate[5:], 16)
    except ValueError:
        return False
    for value in previous[:9]:
        if not isinstance(value, str) or len(value) != 69 or not value.startswith("als1:"):
            continue
        try:
            if (number ^ int(value[5:], 16)).bit_count() <= SIBLING_LAYOUT_DISTANCE:
                return True
        except ValueError:
            continue
    return False


def stage1_visual_dhash(raw_png: bytes) -> str:
    """Reuse the G5 transparent-artwork dHash and an explicit version prefix.

    G3's ROI helper intentionally limits source images to 12 MB. Native Stage1
    PNGs may legitimately be larger (up to G5's 25 MB); the existing G5 dHash
    already crops transparent margins and uses only a 9x8 low-cost thumbnail.
    """
    if not isinstance(raw_png, bytes) or not 0 < len(raw_png) <= MAX_PNG_BYTES:
        raise ValueError("invalid_stage1_visual_source")
    try:
        with Image.open(BytesIO(raw_png)) as image:
            if (image.format != "PNG" or image.width < 8 or image.height < 8
                    or image.width * image.height > 8_000_000):
                raise ValueError("invalid_stage1_visual_source")
            image.load()
            rgba = image.convert("RGBA")
            if rgba.getchannel("A").getbbox() is None:
                raise ValueError("empty_stage1_artwork")
            return "tdh1:" + _rgba_dhash(rgba)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid_stage1_visual_source") from exc


def matches_rejected_visual(candidate: str, rejected_hashes: list[str] | tuple[str, ...]) -> bool:
    if not is_typography_dhash(candidate):
        return False
    return any(candidate_matches(candidate, item, threshold=SIMILARITY_DISTANCE)
               for item in rejected_hashes[:400] if is_typography_dhash(item))
