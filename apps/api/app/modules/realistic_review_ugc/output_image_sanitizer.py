"""Lossless-in-intent thumbnail preparation for original Stage 1 PNG artwork.

Only downsample existing PNG pixels. Never infer transparency from black or
modify a saved/queued original. The renderer's alpha and stitch details are
authoritative; downloaded masters always remain byte-for-byte unchanged.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_PIXELS = 12_000_000


def stage1_png_thumbnail_bytes(original_bytes: bytes, size_pixels: int) -> bytes:
    """Lossless PNG preview with original alpha and aspect ratio."""
    return _stage1_thumbnail_bytes(original_bytes, size_pixels, output_format="PNG")


def stage1_webp_thumbnail_bytes(original_bytes: bytes, size_pixels: int) -> bytes:
    """Small WebP preview only; never change the master PNG or its alpha."""
    return _stage1_thumbnail_bytes(original_bytes, size_pixels, output_format="WEBP")


def _stage1_thumbnail_bytes(original_bytes: bytes, size_pixels: int, *, output_format: str) -> bytes:
    """Resize existing PNG pixels only, without matte removal or fabrication.

    Reject malformed/oversized sources; the caller can stream the original.
    The lossy WebP variant is exclusively for small UI tiles, not embroidery
    geometry, full-size inspection, or downloads.
    """
    if not original_bytes or len(original_bytes) > MAX_PREVIEW_BYTES:
        raise ValueError("Stage 1 original exceeds the thumbnail byte limit")
    if size_pixels < 128 or size_pixels > 1024:
        raise ValueError("Stage 1 thumbnail dimension outside 128-1024")

    try:
        with Image.open(BytesIO(original_bytes)) as original:
            if original.format != "PNG":
                raise ValueError("Stage 1 thumbnail requires the source PNG")
            width, height = original.size
            if width < 1 or height < 1 or width * height > MAX_PREVIEW_PIXELS:
                raise ValueError("Stage 1 PNG pixel count is invalid")
            original.load()
            if original.mode in {"RGBA", "RGB"}:
                preview = original.copy()
            else:
                # Palette and grayscale PNGs can carry a tRNS transparency
                # chunk. Preserve it instead of flattening to opaque RGB.
                has_alpha = "A" in original.getbands() or "transparency" in original.info
                preview = original.convert("RGBA" if has_alpha else "RGB")
            preview.thumbnail((size_pixels, size_pixels), Image.Resampling.LANCZOS)
            output = BytesIO()
            if output_format == "WEBP":
                # WebP preserves RGBA transparency and typically reduces gallery
                # transfer size significantly versus a full-color PNG thumbnail.
                preview.save(output, format="WEBP", quality=80, method=4, exact=True)
            else:
                preview.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Stage 1 original PNG cannot be decoded") from exc