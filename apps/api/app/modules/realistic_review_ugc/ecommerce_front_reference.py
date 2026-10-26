"""Deterministic, bounded front embroidery reference for Ecommerce generation.

The transient React canvas is not a storage asset. Reconstruct the approved
front guide from the verified stock + source artwork, then persist these bytes
before invoking Codex. This is a reference, never a claim of final embroidery QA.
"""
from __future__ import annotations

from io import BytesIO
from collections.abc import Sequence
from app.modules.realistic_review_ugc.front_placement import DEFAULT_FRONT_PLACEMENT, validate_front_placement

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

FRONT_GUIDE = tuple(DEFAULT_FRONT_PLACEMENT[key] for key in ("left", "top", "width", "height"))
MAX_PIXELS = 36_000_000


class FrontReferenceError(ValueError):
    pass


def _validated_rgba(content: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(content)) as raw:
            width, height = raw.size
            if (raw.format not in {"PNG", "JPEG", "WEBP"}
                    or min(width, height) < 1 or width * height > MAX_PIXELS):
                raise FrontReferenceError("Invalid front reference input image")
            raw.load()
            return ImageOps.exif_transpose(raw).convert("RGBA")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise FrontReferenceError("Unreadable front reference image") from exc


def _visible_artwork(art: Image.Image) -> Image.Image:
    """Trim transparent/near-solid margins, matching Blueprint's visible fit."""
    alpha = art.getchannel("A")
    if alpha.getextrema()[0] >= 245:
        rgb = art.convert("RGB")
        background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        diff = ImageChops.difference(rgb, background)
        channels = diff.split()
        # A pixel is considered painted when any RGB channel differs by >=27.
        strongest = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        alpha = strongest.point(lambda value: 255 if value >= 27 else 0)
    visible = alpha.point(lambda value: 255 if value >= 24 else 0).getbbox()
    if visible is None:
        raise FrontReferenceError("Embroidery artwork has no visible pixels")
    x0, y0, x1, y1 = visible
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
    x1, y1 = min(art.width, x1 + 1), min(art.height, y1 + 1)
    trimmed = art.crop((x0, y0, x1, y1))
    trimmed.putalpha(alpha.crop((x0, y0, x1, y1)))
    return trimmed


def render_front_reference(stock_bytes: bytes, artwork_bytes: bytes,
                           guide: Sequence[float] = FRONT_GUIDE) -> bytes:
    """Render PNG with all painted pixels inside the calibrated front area.

    The painted design preserves its native aspect ratio, occupies at most
    95% x 92% of the front guide, and never gets resized to fill the entire cap.
    """
    stock = _validated_rgba(stock_bytes)
    artwork = _visible_artwork(_validated_rgba(artwork_bytes))
    if len(guide) != 4:
        raise FrontReferenceError("Front placement requires four normalized coordinates")
    try:
        validated = validate_front_placement({
            "left": guide[0], "top": guide[1], "width": guide[2], "height": guide[3],
            "upper_line": DEFAULT_FRONT_PLACEMENT["upper_line"],
            "lower_line": DEFAULT_FRONT_PLACEMENT["lower_line"],
        })
    except (ValueError, TypeError) as exc:
        raise FrontReferenceError("Front placement is invalid") from exc
    left, top, width, height = (validated[key] for key in ("left", "top", "width", "height"))
    guide_x = round(stock.width * left)
    guide_y = round(stock.height * top)
    guide_w = round(stock.width * width)
    guide_h = round(stock.height * height)
    scale = min(guide_w * .95 / artwork.width, guide_h * .92 / artwork.height)
    target_w = max(1, min(guide_w, round(artwork.width * scale)))
    target_h = max(1, min(guide_h, round(artwork.height * scale)))
    resized = artwork.resize((target_w, target_h), Image.Resampling.LANCZOS)
    x = guide_x + (guide_w - target_w) // 2
    y = guide_y + (guide_h - target_h) // 2
    stock.alpha_composite(resized, (x, y))
    output = BytesIO()
    stock.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
