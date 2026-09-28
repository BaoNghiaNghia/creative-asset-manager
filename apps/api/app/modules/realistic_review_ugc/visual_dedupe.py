from __future__ import annotations

from io import BytesIO
from typing import Iterable

from PIL import Image, ImageOps

_FINGERPRINT_SIZE = 16
_NEAR_DUPLICATE_DISTANCE = 18


def _dhash(image: Image.Image, size: int = _FINGERPRINT_SIZE) -> str:
    gray = ImageOps.grayscale(image).resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
    bits = 0
    for y in range(size):
        row = y * (size + 1)
        for x in range(size):
            bits = (bits << 1) | int(pixels[row + x] > pixels[row + x + 1])
    return f"{bits:0{size * size // 4}x}"


def visual_fingerprints(image_bytes: bytes) -> list[str]:
    """Return resize/re-encode and moderate-crop tolerant perceptual fingerprints."""
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            fingerprints = [_dhash(image)]
            # Pinterest reposts are frequently the same photo with a small edge crop.
            for margin in (0.04, 0.08):
                left = int(width * margin)
                top = int(height * margin)
                right = width - left
                bottom = height - top
                if right - left >= 24 and bottom - top >= 24:
                    fingerprints.append(_dhash(image.crop((left, top, right, bottom))))
            return list(dict.fromkeys(fingerprints))
    except (OSError, ValueError):
        return []


def hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return 10_000
    return (int(left, 16) ^ int(right, 16)).bit_count()


def is_visual_near_duplicate(
    fingerprints: Iterable[str],
    existing_fingerprints: Iterable[str],
    *,
    max_distance: int = _NEAR_DUPLICATE_DISTANCE,
) -> bool:
    current = [value for value in fingerprints if value]
    existing = [value for value in existing_fingerprints if value]
    return any(hamming_distance(a, b) <= max_distance for a in current for b in existing)
