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
    """Resize the original PNG to a bounding square, preserving aspect/alpha.

    Raises ValueError on untrusted, oversized or unsupported inputs, allowing
    the endpoint to stream the original instead of producing a damaged preview.
    No matte removal, RGB flattening, or background compositing is performed.
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
            preview.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Stage 1 original PNG cannot be decoded") from exc
