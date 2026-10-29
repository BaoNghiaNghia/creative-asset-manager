"""Low-cost, deterministic near-duplicate advisory for Typography Style DNA.

Only the analyzer-selected lettering ROI is hashed. A dHash match is a *warning*
for human review, NOT proof of plagiarism or a reason to auto-reject a source.
The `tdh1:` prefix versions this 64-bit algorithm; never compare hashes from
different versions. No extra provider call or source text is persisted.
"""
from __future__ import annotations

from io import BytesIO
import math
import re

from PIL import Image, ImageOps, UnidentifiedImageError

HASH_VERSION = "tdh1:"
_HASH_PATTERN = re.compile(r"^tdh1:[0-9a-f]{16}$")
MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_DECODE_PIXELS = 20_000_000
MAX_DISTANCE = 8


def is_typography_dhash(value: str | None) -> bool:
    return isinstance(value, str) and _HASH_PATTERN.fullmatch(value) is not None


def typography_dhash(image_bytes: bytes, region: dict) -> str:
    """64-bit horizontal difference hash from EXIF-corrected, normalized ROI.

    Region values are assumed validated by the Style DNA contract. An
    independent boundary check still prevents out-of-range or degenerate
    crops when the algorithm is reused by other callers.
    """
    if not image_bytes or len(image_bytes) > MAX_SOURCE_BYTES:
        raise ValueError("invalid_typography_image_size")
    if not isinstance(region, dict):
        raise ValueError("invalid_typography_region")
    try:
        x, y, w, h = (float(region[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_typography_region") from exc
    if (not all(math.isfinite(v) for v in (x, y, w, h))
            or not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1)
            or x + w > 1.000001 or y + h > 1.000001):
        raise ValueError("invalid_typography_region")
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("unsupported_typography_image")
            if (source.width < 8 or source.height < 8
                    or source.width * source.height > MAX_DECODE_PIXELS):
                raise ValueError("invalid_typography_image_dimensions")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            # Composite transparent lettering consistently, avoiding the
            # accidental black background from RGB conversion of alpha.
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            background.alpha_composite(image)
            width, height = background.size
            left = max(0, min(width - 1, int(x * width)))
            top = max(0, min(height - 1, int(y * height)))
            right = max(left + 1, min(width, math.ceil((x + w) * width)))
            bottom = max(top + 1, min(height, math.ceil((y + h) * height)))
            gray = background.crop((left, top, right, bottom)).convert("L")
            sample = gray.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(sample.getdata())
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid_typography_image") from exc
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return HASH_VERSION + f"{bits:016x}"


def has_stale_roi_hash(manual_review: object) -> bool:
    """Do not compare an AI-region fingerprint after a human ROI correction.

    Invalid existing review JSON is treated as stale (fail closed). A future
    local rehash from the corrected region can explicitly resolve the flag.
    """
    if manual_review is None:
        return False
    if not isinstance(manual_review, dict):
        return True
    override = manual_review.get("override")
    if override is None:
        return False
    if not isinstance(override, dict):
        return True
    return "typography_region" in override


def typography_hamming_distance(left: str, right: str) -> int:
    """Only compare valid same-version ROI hashes."""
    if not is_typography_dhash(left) or not is_typography_dhash(right):
        raise ValueError("incompatible_typography_dhash")
    return (int(left[5:], 16) ^ int(right[5:], 16)).bit_count()


def candidate_matches(source_hash: str, candidate_hash: str, *, threshold: int = 6) -> bool:
    if not 0 <= threshold <= MAX_DISTANCE:
        raise ValueError("invalid_similarity_threshold")
    return (
        is_typography_dhash(source_hash)
        and is_typography_dhash(candidate_hash)
        and typography_hamming_distance(source_hash, candidate_hash) <= threshold
    )
