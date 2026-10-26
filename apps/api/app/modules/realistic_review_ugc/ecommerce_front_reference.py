"""Deterministic, bounded front embroidery reference for Ecommerce generation.

The transient React canvas is not a storage asset. Reconstruct the approved
front guide from the verified stock + source artwork, then persist these bytes
before invoking Codex. This is a reference, never a claim of final embroidery QA.
"""
from __future__ import annotations

from io import BytesIO
from collections.abc import Sequence
from app.modules.realistic_review_ugc.front_placement import (DEFAULT_FRONT_PLACEMENT, validate_front_placement, validate_front_design_size)

from PIL import Image, ImageChops, ImageOps, ImageFilter, ImageStat, UnidentifiedImageError
from app.modules.realistic_review_ugc.stage1_fabric import extract_thread_geometry

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
            oriented = ImageOps.exif_transpose(raw)
            rgba = oriented.convert("RGBA")
            rgba.info.update(oriented.info)  # Preserve in-band thread geometry.
            return rgba
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise FrontReferenceError("Unreadable front reference image") from exc


def _visible_artwork(art: Image.Image) -> Image.Image:
    """Trim transparent/near-solid margins, matching Blueprint's visible fit."""
    # Fabric-first PNGs have no visible alpha; use their verified canonical
    # embroidery geometry, not color thresholding of a woven fabric photo.
    try:
        geometry = extract_thread_geometry(art)
    except ValueError as exc:
        raise FrontReferenceError("Invalid Fabric-First thread geometry") from exc
    if geometry is not None:
        art = geometry
    alpha = art.getchannel("A")
    if alpha.getextrema()[0] >= 245:
        rgb = art.convert("RGB")
        corner = rgb.crop((0, 0, min(128, rgb.width), min(128, rgb.height)))
        sample = ImageStat.Stat(corner)
        # A cropped fabric photograph can look matte-like but contains
        # thousands of woven micro-edges. Without verified Stage 1 geometry,
        # never risk stamping its rectangular swatch onto a different hat.
        if (max(sample.stddev) > 8.0
                and max(sample.mean) - min(sample.mean) < 38):
            raise FrontReferenceError(
                "Textured fabric artwork requires verified source thread geometry"
            )
        background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        diff = ImageChops.difference(rgb, background)
        channels = diff.split()
        # Legacy opaque artwork without Fabric-First metadata: conservative
        # near-solid matte removal only. Never use for tagged fabric photos.
        strongest = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        # Cloth photographs contain woven micro-contrast. A low 27-point
        # threshold lifts the entire fabric, producing a rectangular patch.
        # Fabric-first Stage 1 uses blue/red embroidery on neutral beige twill.
        # Require substantial contrast, with smooth antialiased edges.
        alpha = strongest.point(lambda value: max(0, min(255, (value - 44) * 5)))
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
                           guide: Sequence[float] = FRONT_GUIDE,
                           *, design_size: int = 100) -> bytes:
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
    base = min(guide_w * .95 / artwork.width, guide_h * .92 / artwork.height)
    zoom = validate_front_design_size(design_size) / 100.0
    # Exactly the same contain-fit and 98% zoom limits as BlueprintDesignFit.
    scale = base * min(zoom,
                       guide_w * .98 / (artwork.width * base),
                       guide_h * .98 / (artwork.height * base))
    target_w = max(1, min(guide_w, round(artwork.width * scale)))
    target_h = max(1, min(guide_h, round(artwork.height * scale)))
    resized = artwork.resize((target_w, target_h), Image.Resampling.LANCZOS)
    x = guide_x + (guide_w - target_w) // 2
    y = guide_y + (guide_h - target_h) // 2
    # Match contact shadow to local cap textile, rather than importing the
    # original reference's beige fabric. The guide is only placement evidence
    # for the full photo generator, not a production digitizing claim.
    local_mask = resized.getchannel("A")
    contact = ImageChops.subtract(
        local_mask.filter(ImageFilter.GaussianBlur(1.2)), local_mask
    ).point(lambda value: int(value * .11))
    background = stock.crop((x, y, x + target_w, y + target_h))
    dark = ImageChops.multiply(
        background.convert("RGB"), Image.new("RGB", background.size, (210, 210, 210))
    ).convert("RGBA")
    background.paste(dark, (0, 0), contact)
    stock.paste(background, (x, y))
    stock.alpha_composite(resized, (x, y))
    output = BytesIO()
    stock.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
