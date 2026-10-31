"""Small, alpha-preserving reference thumbnails for CAM Typography Ideas.

Source images and original download URLs are never modified. Decode is bounded
by source bytes and pixel count; callers retain tenant-scoped authorization.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_REFERENCE_THUMB_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_THUMB_PIXELS = 20_000_000


def typography_thumbnail_webp(source: bytes, size: int) -> bytes:
    if not (128 <= size <= 640) or not source or len(source) > MAX_REFERENCE_THUMB_BYTES:
        raise ValueError("Reference thumbnail bounds exceeded")
    try:
        with Image.open(BytesIO(source)) as original:
            if original.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Unsupported typography reference image")
            width, height = original.size
            if width < 1 or height < 1 or width * height > MAX_REFERENCE_THUMB_PIXELS:
                raise ValueError("Reference dimensions exceed preview limits")
            corrected = ImageOps.exif_transpose(original)
            transparent = ("A" in corrected.getbands() or "transparency" in corrected.info)
            preview = corrected.convert("RGBA" if transparent else "RGB")
            preview.thumbnail((size, size), Image.Resampling.LANCZOS)
            out = BytesIO()
            preview.save(out, "WEBP", quality=79, method=4, exact=True)
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("Reference image could not be decoded") from exc
