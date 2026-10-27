"""Fabric-bound Stage 2 Products Slot 1 embroidery only.

One deterministic stitch-oriented material pass, one model refinement, then a
localized final composite.  No Stage 1 writes, no shared legacy slot changes.
This is a photographic material approximation, not a digitized machine file or
a claim of textile manufacturing accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.modules.realistic_review_ugc.ecommerce_front_reference import (
    FrontReferenceError, _validated_rgba, _visible_artwork,
)
from app.modules.realistic_review_ugc.front_placement import (
    DEFAULT_FRONT_PLACEMENT, validate_front_design_size, validate_front_placement,
)
from app.modules.realistic_review_ugc.stage1_photo_samples import reference_patch

_MAX_PIXELS = 8_000_000  # Bounded float planes for production worker memory.


class Slot1EmbroideryQAError(ValueError):
    """The candidate failed a measurable quality gate; never store it."""


@dataclass(frozen=True)
class Slot1MaterialPass:
    """Job-local buffers, never persisted over the Stage 1 design or stock."""

    stock: Image.Image
    stitched: Image.Image
    thread_mask: Image.Image
    edit_mask: Image.Image
    guide_box: tuple[int, int, int, int]

    def stitched_png(self) -> bytes:
        return _png(self.stitched)

    def edit_mask_png(self) -> bytes:
        return _png(self.edit_mask)


def _png(image: Image.Image) -> bytes:
    destination = BytesIO()
    image.save(destination, format="PNG", optimize=True)
    return destination.getvalue()


def _fitted_artwork(
    stock: Image.Image, source: bytes, placement: dict,
) -> tuple[Image.Image, tuple[int, int], tuple[int, int, int, int]]:
    artwork = _visible_artwork(_validated_rgba(source))
    params = validate_front_placement({
        key: placement.get(key, value)
        for key, value in DEFAULT_FRONT_PLACEMENT.items()
    })
    zoom = validate_front_design_size(placement.get("design_size", 100))
    guide_x = round(stock.width * params["left"])
    guide_y = round(stock.height * params["top"])
    guide_w = round(stock.width * params["width"])
    guide_h = round(stock.height * params["height"])
    if min(guide_w, guide_h) < 8:
        raise FrontReferenceError("Front embroidery placement is too small for stitch simulation.")
    base = min(guide_w * .95 / artwork.width, guide_h * .92 / artwork.height)
    scale = base * min(
        zoom / 100.0,
        guide_w * .98 / (artwork.width * base),
        guide_h * .98 / (artwork.height * base),
    )
    target_w = max(1, min(guide_w, round(artwork.width * scale)))
    target_h = max(1, min(guide_h, round(artwork.height * scale)))
    resized = artwork.resize((target_w, target_h), Image.Resampling.LANCZOS)
    x = guide_x + (guide_w - target_w) // 2
    y = guide_y + (guide_h - target_h) // 2
    return resized, (x, y), (guide_x, guide_y, guide_x + guide_w, guide_y + guide_h)


def build_slot1_material_pass(
    stock_bytes: bytes, artwork_bytes: bytes, placement: dict,
) -> Slot1MaterialPass:
    """Produce a low-profile thread foundation using Stage 1 geometry only.

    Directional structure tensors approximate which way satin stitches cross
    the stroke; broad fills receive restrained directional tatami variation.
    No fixed font-dependent changes or fake solid-letter extrusion.
    """
    stock = _validated_rgba(stock_bytes).convert("RGB")
    if stock.width * stock.height > _MAX_PIXELS:
        raise FrontReferenceError("Stock photo exceeds Slot 1 material processing limit.")
    artwork, (x, y), guide_box = _fitted_artwork(stock, artwork_bytes, placement)
    w, h = artwork.size
    if w * h > _MAX_PIXELS:
        raise FrontReferenceError("Embroidery area exceeds pixel limit.")

    source = np.asarray(artwork, dtype=np.float32)
    a = source[:, :, 3] / 255.0
    if float(np.max(a)) < .1:
        raise FrontReferenceError("Embroidery artwork contains no visible strokes.")
    binary = np.uint8(a > .18)
    thickness = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    smoothed = cv2.GaussianBlur(a, (0, 0), 1.6)
    gx = cv2.Sobel(smoothed, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smoothed, cv2.CV_32F, 0, 1, ksize=3)
    # The angle is an *axis*, not a signed vector: mix it in double-angle
    # space to avoid discontinuities at +/-90 degrees. Thin serifs keep
    # stroke-normal satin, broad letters receive directional fill.
    radius = 2.8
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), radius)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), radius)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), radius)
    normal_length = np.sqrt((jxx - jyy) ** 2 + (2 * jxy) ** 2) + 1e-5
    fill_weight = np.clip((thickness - 2.0) / 5.0, 0, 1)
    axis_cos = (1 - fill_weight) * ((jxx - jyy) / normal_length) + fill_weight * np.cos(1.24)
    axis_sin = (1 - fill_weight) * ((2 * jxy) / normal_length) + fill_weight * np.sin(1.24)
    angle = .5 * np.arctan2(axis_sin, axis_cos)
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    across = -xx * np.sin(angle) + yy * np.cos(angle)
    along = xx * np.cos(angle) + yy * np.sin(angle)
    # Slight variation in stitch spacing is essential: mathematically perfect
    # stripes make lettering look digitally printed or molded. Preserve the
    # canonical alpha; texture never defines new lettering.
    seed = int.from_bytes(hashlib.sha256(np.uint8(np.clip(source, 0, 255)).tobytes()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    random_grain = rng.standard_normal((h, w)).astype(np.float32)
    fine_grain = random_grain - cv2.GaussianBlur(random_grain, (0, 0), 1.05)
    jitter = .20 * np.sin(xx * .059 + yy * .045) + .13 * np.cos(xx * .027 - yy * .083)
    phase = (2 * np.pi / 2.65) * (across + jitter)
    strand = .072 * np.cos(phase) + .027 * np.cos(2 * phase + .34)
    needle = .014 * np.cos(2 * np.pi * along / 13.4 + .22 * np.sin(xx * .043))
    # Reference is a tiny grayscale swatch of real stitches; transfer only
    # high-frequency surface variation, never its words, pigment or fabric.
    sample = np.asarray(reference_patch("thread"), dtype=np.float32)
    mirrored = np.block([[sample, sample[:, ::-1]], [sample[::-1, :], sample[::-1, ::-1]]])
    tiled = np.tile(mirrored, ((h + 127) // 128, (w + 127) // 128))[:h, :w]
    photo_grain = np.clip(
        (tiled - cv2.GaussianBlur(tiled, (0, 0), 1.1)) / 110.0, -1, 1,
    )
    edge = np.clip(cv2.GaussianBlur(a, (0, 0), 1.0) - a, -.5, .5)
    light = strand + needle + .025 * photo_grain + .012 * fine_grain
    light = np.clip(light, -.15, .15) - .012 * np.maximum(-edge, 0)
    cap = np.asarray(stock.crop((x, y, x + w, y + h)), dtype=np.float32)
    # Stage 1 is the glyph/color authority, NOT the lighting authority.
    # Its RGB may already include 3D bevels or broad satin highlights.
    # Recover a stable pigment per hue group from opaque stroke cores, then
    # synthesize only the fine directional fibers above. Never inherit
    # pixel-wise bevel shading from Stage 1.
    rgb = np.uint8(np.clip(np.rint(source[:, :, :3]), 0, 255))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    core = a >= .90
    pigment = rgb.astype(np.float32).copy()
    if np.count_nonzero(core) >= 24:
        # Fine hue buckets preserve independently colored design elements,
        # while collapsing light/dark bevel variations of the same ink.
        hue_bin = hsv[:, :, 0].astype(np.int16) // 12
        saturation_bin = (hsv[:, :, 1] >= 45).astype(np.int16)
        groups = hue_bin + saturation_bin * 16
        for group in np.unique(groups[core]):
            members = core & (groups == group)
            if np.count_nonzero(members) < 12:
                continue
            representative = np.median(rgb[members], axis=0)
            pigment[groups == group] = representative
    # Build a low-profile stitched material, not a second embossed shader.
    # Subpixel filaments need an additive photographic highlight/shadow term.
    # A pure percent-of-pigment shader barely changes dark blue/red thread and
    # was the root cause of low-contrast, digitally printed-looking letters.
    filament_glints = np.clip(
        10.5 * np.cos(phase + .17) + 4.5 * np.cos(2 * phase + .34),
        -16, 16,
    )
    thread = pigment * (1.0 + light[:, :, None]) + filament_glints[:, :, None]
    thread = .94 * thread + .06 * cap
    thread = np.clip(thread, 0, 255)
    # Only a slight real contact depression, never broad black outlines.
    touching = np.maximum(cv2.GaussianBlur(a, (0, 0), 1.1) - a, 0)
    substrate = cap * (1 - .075 * touching[:, :, None])
    blend_alpha = np.clip(a * .965, 0, 1)
    result = thread * blend_alpha[:, :, None] + substrate * (1 - blend_alpha[:, :, None])
    stitched = stock.copy()
    stitched.paste(Image.fromarray(np.uint8(np.clip(np.rint(result), 0, 255)), "RGB"), (x, y))

    full_mask = Image.new("L", stock.size, 0)
    full_mask.paste(Image.fromarray(np.uint8(np.rint(a * 255)), "L"), (x, y))
    # A two-pixel fabric-contact allowance only, clipped to the saved field.
    mask = np.asarray(full_mask, dtype=np.uint8)
    dilated = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    left, top, right, bottom = guide_box
    allowed = np.zeros(mask.shape, dtype=np.uint8)
    allowed[top:bottom, left:right] = 255
    dilated = cv2.bitwise_and(dilated, allowed)
    edit_mask = Image.fromarray(dilated, "L")
    # Do not change a single stock pixel outside the exact edit allowance.
    sealed = np.array(stitched, dtype=np.uint8, copy=True)
    stock_pixels = np.asarray(stock)
    sealed[dilated == 0] = stock_pixels[dilated == 0]
    stitched = Image.fromarray(sealed, "RGB")
    return Slot1MaterialPass(stock, stitched, full_mask, edit_mask, guide_box)


def finish_slot1_refinement(
    material: Slot1MaterialPass, generated_bytes: bytes,
) -> bytes:
    """Retain only bounded micro-fiber light variation from the AI candidate.

    Exact underlying stock pixels, text silhouette, original color and location
    come from the deterministic first pass, not from model hallucinations.
    Gate blank/refused generations; do not silently accept fake success.
    """
    try:
        with Image.open(BytesIO(generated_bytes)) as raw:
            if raw.format not in {"PNG", "JPEG", "WEBP"}:
                raise Slot1EmbroideryQAError("Unsupported AI refinement format.")
            if min(raw.size) < 256 or raw.width * raw.height > _MAX_PIXELS:
                raise Slot1EmbroideryQAError("AI refinement dimensions are invalid.")
            raw.load()
            candidate = ImageOps.exif_transpose(raw).convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise Slot1EmbroideryQAError("Cannot decode AI refinement.") from exc

    same_frame = candidate.size == material.stock.size
    if not same_frame:
        # Some imagegen providers choose a square canvas even for a 4:5 edit
        # target.  Only sample its subtle fiber frequencies.  NEVER transfer
        # full-color geometry or its generated cap/background pixels.
        candidate = candidate.resize(material.stock.size, Image.Resampling.LANCZOS)
    stock = np.asarray(material.stock, dtype=np.float32)
    underlay = np.asarray(material.stitched, dtype=np.float32)
    generated = np.asarray(candidate, dtype=np.float32)
    alpha = np.asarray(material.thread_mask, dtype=np.float32) / 255.0
    inside = alpha >= .72
    if int(np.count_nonzero(inside)) < 24:
        raise Slot1EmbroideryQAError("The designed embroidery has insufficient visible area.")
    intended = underlay[inside] - stock[inside]
    observed = generated[inside] - stock[inside]
    contrast = float(np.sqrt(np.mean(intended ** 2)))
    if contrast > 12:
        # A blank hat, wrong-color wordmark or missing lettering has no
        # directional agreement with the source, even with global brightness.
        fidelity = float(np.sum(observed * intended) / (np.sum(intended ** 2) + 1e-6))
        if fidelity < .13:
            raise Slot1EmbroideryQAError("AI candidate lost the original design or thread pigments.")
    # Reject an AI-created heavy dark halo when source and generated geometry
    # align. The final compositing always excludes such oversized shadows.
    if same_frame:
        core = np.uint8(alpha > .50)
        near = cv2.dilate(core, np.ones((7, 7), np.uint8)).astype(bool) & ~core.astype(bool)
        far = cv2.dilate(core, np.ones((25, 25), np.uint8)).astype(bool) & ~cv2.dilate(
            core, np.ones((17, 17), np.uint8)).astype(bool)
        left, top, right, bottom = material.guide_box
        field = np.zeros(core.shape, dtype=bool)
        field[top:bottom, left:right] = True
        near &= field
        far &= field
        if np.count_nonzero(near) > 40 and np.count_nonzero(far) > 40:
            diff = np.mean(stock - generated, axis=2)
            if float(np.median(diff[near]) - np.median(diff[far])) > 32:
                raise Slot1EmbroideryQAError("AI candidate has an exaggerated dark emboss halo.")

    # The old .33*(generated-highpass) removed nearly all visible thread
    # detail, leaving a smooth colored wordmark. Transfer bounded high-frequency
    # *difference from stock* so cap weave/lighting and invented glyphs cannot
    # be pasted from the AI result. Geometry and pigment still come from pass 1.
    luma = cv2.cvtColor(np.uint8(generated), cv2.COLOR_RGB2GRAY).astype(np.float32)
    stock_luma = cv2.cvtColor(np.uint8(stock), cv2.COLOR_RGB2GRAY).astype(np.float32)
    embroidery_difference = luma - stock_luma
    micro = embroidery_difference - cv2.GaussianBlur(embroidery_difference, (0, 0), 1.45)
    micro = np.clip(micro, -24, 24)
    # Confine the extra filament relief to the opaque source glyph cores:
    # antialiased edges retain their exact alpha and no dark halo can grow.
    core_weight = np.clip((alpha - .18) / .82, 0, 1)
    final = underlay + (.72 * micro * core_weight)[:, :, None]
    result = np.uint8(np.clip(np.rint(final), 0, 255))
    # Verify non-edit pixels are truly stock-identical (including the side logo).
    editable = np.asarray(material.edit_mask, dtype=np.uint8) > 0
    stock_bytes = np.asarray(material.stock, dtype=np.uint8)
    result[~editable] = stock_bytes[~editable]
    if not np.array_equal(result[~editable], stock_bytes[~editable]):
        raise Slot1EmbroideryQAError("The cap was modified outside the pinned embroidery mask.")
    if np.mean(np.abs(result[inside].astype(np.float32) - stock[inside])) < 5:
        raise Slot1EmbroideryQAError("Finished image lost embroidery contrast.")
    # When there is enough stroke width to resolve individual threads,
    # reject an essentially untextured/printed fill. This measurable gate
    # protects v2 from regressing to the smooth v1 look. Tiny lettering is
    # exempt because its stitch filaments are subpixel at catalog scale.
    core = np.uint8(alpha >= .82)
    thick = cv2.distanceTransform(core, cv2.DIST_L2, 3) >= 3.0
    if int(np.count_nonzero(thick)) >= 512:
        luminance = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY).astype(np.float32)
        high_frequency = luminance - cv2.GaussianBlur(luminance, (0, 0), 1.15)
        if float(np.std(high_frequency[thick])) < 10.5:
            raise Slot1EmbroideryQAError("Finished embroidery is too flat or printed-looking.")
    return _png(Image.fromarray(result, "RGB"))
