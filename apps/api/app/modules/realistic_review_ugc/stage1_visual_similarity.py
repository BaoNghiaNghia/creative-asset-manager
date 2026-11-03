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
