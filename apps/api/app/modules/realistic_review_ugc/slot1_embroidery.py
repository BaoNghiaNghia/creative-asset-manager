"""Photographic Stage 2 / Products Slot 1 image edit.

Only the selected Stage 1 artwork determines glyph geometry and thread colors.
A pigment-only guide is given to imagegen, NOT a computer-generated stitch
simulation. The model must generate the fiber material; the worker validates
and composites it exclusively within the pinned original artwork silhouette.
No other Ecommerce slot or previously completed output is changed.
"""
from __future__ import annotations

from dataclasses import dataclass
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

_MAX_PIXELS = 8_000_000


class Slot1EmbroideryQAError(ValueError):
    """Model output is not a verified photo-edit; do not publish it."""


def _png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


@dataclass(frozen=True)
class Slot1MaterialPass:
    stock: Image.Image
    stitched: Image.Image  # Pigment-only, temporary input guide, not the final photo.
    thread_mask: Image.Image
    edit_mask: Image.Image
    guide_box: tuple[int, int, int, int]
    pigment: Image.Image
    contact_width_px: int = 0

    def stitched_png(self) -> bytes:
        """Retain previous internal API for QA, not for publishing."""
        return _png(self.stitched)

    def _square(self, image: Image.Image, background: int | tuple[int, int, int]) -> Image.Image:
        side = max(self.stock.size)
        pad_x = (side - self.stock.width) // 2
        pad_y = (side - self.stock.height) // 2
        canvas = Image.new(image.mode, (side, side), background)
        canvas.paste(image, (pad_x, pad_y))
        return canvas

    def model_edit_target_png(self) -> bytes:
        """Square letterbox, so square imagegen outputs need no geometric distortion."""
        return _png(self._square(self.stitched, (255, 255, 255)))

    def edit_mask_png(self) -> bytes:
        return _png(self._square(self.edit_mask, 0))


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
        raise FrontReferenceError("Front embroidery placement is too small.")
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


def _canonical_pigment(source: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Collapse any broad digital bevel into the original color groups, no faux fibers."""
    rgb = np.uint8(np.clip(np.rint(source[:, :, :3]), 0, 255))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    core = alpha >= .90
    pigment = rgb.astype(np.float32).copy()
    if int(np.count_nonzero(core)) < 24:
        return pigment
    # Color bucketing applies only to the same hue/saturation group;
    # independently colored letters retain independent pigment.
    groups = hsv[:, :, 0].astype(np.int16) // 12 + 16 * (hsv[:, :, 1] >= 45)
    for group in np.unique(groups[core]):
        members = core & (groups == group)
        if int(np.count_nonzero(members)) >= 12:
            pigment[groups == group] = np.median(rgb[members], axis=0)
    return pigment


def build_slot1_material_pass(
    stock_bytes: bytes, artwork_bytes: bytes, placement: dict,
    *, contact_width_px: int = 0,
) -> Slot1MaterialPass:
    """Create a pigment-only edit guide and immutable protection mask.

    NO wave/stripe, embossed fill, synthetic thread texture, repeated sample,
    shadow extrusion, or fabricated needle marks appear in this preprocessing.
    """
    if contact_width_px not in (0, 2):
        raise ValueError("Only legacy 0px and v4 2px contact bands are supported.")
    stock = _validated_rgba(stock_bytes).convert("RGB")
    if stock.width * stock.height > _MAX_PIXELS:
        raise FrontReferenceError("Stock photo exceeds Slot 1 memory limit.")
    artwork, (x, y), guide_box = _fitted_artwork(stock, artwork_bytes, placement)
    source = np.asarray(artwork, dtype=np.float32)
    alpha = source[:, :, 3] / 255.0
    if float(np.max(alpha)) < .1:
        raise FrontReferenceError("Source contains no visible design strokes.")
    pigment_rgb = _canonical_pigment(source, alpha)
    w, h = artwork.size
    cap = np.asarray(stock.crop((x, y, x + w, y + h)), dtype=np.float32)
    # A temporary location/color guide only. AI is responsible for every fiber.
    guide_alpha = np.clip(alpha * .86, 0, 1)
    composite = pigment_rgb * guide_alpha[..., None] + cap * (1 - guide_alpha[..., None])
    stitched = stock.copy()
    stitched.paste(Image.fromarray(np.uint8(np.rint(composite)), "RGB"), (x, y))
    full_mask = Image.new("L", stock.size, 0)
    full_mask.paste(Image.fromarray(np.uint8(np.rint(alpha * 255)), "L"), (x, y))
    full_pigment = stock.copy()
    full_pigment.paste(Image.fromarray(np.uint8(np.clip(np.rint(pigment_rgb), 0, 255)), "RGB"), (x, y))
    # v3 retains its original 2px protection margin. v4 grants one
    # additional antialias/contact pixel without altering the stock elsewhere.
    raw_mask = np.asarray(full_mask, dtype=np.uint8)
    kernel = 7 if contact_width_px else 5
    dilated = cv2.dilate(raw_mask, np.ones((kernel, kernel), np.uint8))
    left, top, right, bottom = guide_box
    allowed = np.zeros(raw_mask.shape, np.uint8)
    allowed[top:bottom, left:right] = 255
    edit_mask = Image.fromarray(cv2.bitwise_and(dilated, allowed), "L")
    return Slot1MaterialPass(stock, stitched, full_mask, edit_mask, guide_box,
                             full_pigment, contact_width_px)


def _align_candidate(material: Slot1MaterialPass, raw: Image.Image) -> Image.Image:
    """Unpad square model canvases; never stretch a square into a 4:5 hat."""
    candidate = ImageOps.exif_transpose(raw).convert("RGB")
    stock_w, stock_h = material.stock.size
    candidate_aspect = candidate.width / candidate.height
    stock_aspect = stock_w / stock_h
    if candidate.size == (stock_w, stock_h):
        return candidate
    if abs(candidate_aspect - 1) < .025:
        side = max(stock_w, stock_h)
        square = candidate.resize((side, side), Image.Resampling.LANCZOS)
        left = (side - stock_w) // 2
        top = (side - stock_h) // 2
        return square.crop((left, top, left + stock_w, top + stock_h))
    if abs(candidate_aspect - stock_aspect) < .025:
        return candidate.resize((stock_w, stock_h), Image.Resampling.LANCZOS)
    raise Slot1EmbroideryQAError("Image edit changed aspect ratio and cannot be aligned safely.")


def finish_slot1_refinement(material: Slot1MaterialPass, generated_bytes: bytes) -> bytes:
    """Use AI-created thread material, clipped to exact original Stage 1 glyph alpha.

    Refuse identity/no-op edits, incorrect pigment, missing design or fake flat
    prints. The previous pipeline discarded almost all AI pixels and always
    published its artificial underlay; that behavior is intentionally removed.
    """
    try:
        with Image.open(BytesIO(generated_bytes)) as raw:
            if raw.format not in {"PNG", "JPEG", "WEBP"}:
                raise Slot1EmbroideryQAError("Unsupported image edit format.")
            if min(raw.size) < 256 or raw.width * raw.height > _MAX_PIXELS:
                raise Slot1EmbroideryQAError("Image edit dimensions are invalid.")
            raw.load()
            candidate = np.asarray(_align_candidate(material, raw), dtype=np.float32)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise Slot1EmbroideryQAError("Cannot decode AI edit.") from exc
    stock = np.asarray(material.stock, dtype=np.float32)
    guide = np.asarray(material.stitched, dtype=np.float32)
    pigment = np.asarray(material.pigment, dtype=np.float32)
    alpha = np.asarray(material.thread_mask, dtype=np.float32) / 255.0
    core = alpha >= .82
    if int(np.count_nonzero(core)) < 24:
        raise Slot1EmbroideryQAError("Canonical design has insufficient visible glyph area.")

    # Correlation and hue must be correct BEFORE making any pigment correction.
    observed = candidate[core] - stock[core]
    intended = pigment[core] - stock[core]
    fidelity = float(
        np.sum(observed * intended) /
        (np.linalg.norm(observed) * np.linalg.norm(intended) + 1e-6)
    )
    if fidelity < .65:
        raise Slot1EmbroideryQAError("AI edit did not preserve the canonical design/thread colors.")
    if float(np.mean(np.abs(observed))) < 10:
        raise Slot1EmbroideryQAError("AI edit omitted the embroidery.")

    candidate_hsv = cv2.cvtColor(np.uint8(np.clip(candidate, 0, 255)), cv2.COLOR_RGB2HSV)
    pigment_hsv = cv2.cvtColor(np.uint8(np.clip(pigment, 0, 255)), cv2.COLOR_RGB2HSV)
    if np.count_nonzero(core) >= 50:
        orig_s = float(np.median(pigment_hsv[:, :, 1][core]))
        if orig_s >= 55:
            target_hue = float(np.median(pigment_hsv[:, :, 0][core]))
            model_hue = float(np.median(candidate_hsv[:, :, 0][core]))
            hue_error = abs(target_hue - model_hue)
            if min(hue_error, 180 - hue_error) > 19:
                raise Slot1EmbroideryQAError("AI edit changed source embroidery hue.")

    # The model must have produced material detail; the guide itself is not a
    # finished asset and cannot be passed through as a successful AI edit.
    guide_luma = cv2.cvtColor(np.uint8(guide), cv2.COLOR_RGB2GRAY).astype(np.float32)
    candidate_luma = cv2.cvtColor(np.uint8(candidate), cv2.COLOR_RGB2GRAY).astype(np.float32)
    stock_luma = cv2.cvtColor(np.uint8(stock), cv2.COLOR_RGB2GRAY).astype(np.float32)
    high = candidate_luma - cv2.GaussianBlur(candidate_luma, (0, 0), 1.2)
    high_stock = stock_luma - cv2.GaussianBlur(stock_luma, (0, 0), 1.2)
    model_change = candidate - guide
    if float(np.sqrt(np.mean(model_change[core] ** 2))) < 3.0:
        raise Slot1EmbroideryQAError("AI edit made no meaningful photographic refinement.")
    if float(np.std(high[core])) < max(3.2, float(np.std(high_stock[core])) * 1.1):
        raise Slot1EmbroideryQAError("AI edit still resembles flat printed lettering.")

    # Reject large dark emboss halos before the masked composite removes them.
    broad = np.uint8(alpha >= .5)
    near = cv2.dilate(broad, np.ones((7, 7), np.uint8)).astype(bool) & ~(broad.astype(bool))
    far = cv2.dilate(broad, np.ones((23, 23), np.uint8)).astype(bool) & ~cv2.dilate(
        broad, np.ones((15, 15), np.uint8)).astype(bool)
    field = np.zeros(broad.shape, bool)
    left, top, right, bottom = material.guide_box
    field[top:bottom, left:right] = True
    near &= field
    far &= field
    if np.count_nonzero(near) > 40 and np.count_nonzero(far) > 40:
        darkening = np.mean(stock - candidate, axis=2)
        if float(np.median(darkening[near]) - np.median(darkening[far])) > 29:
            raise Slot1EmbroideryQAError("AI edit has a thick embossed shadow halo.")

    # Pigment correction is bounded and global. Keep AI fibers, lighting and
    # organic thread detail, not just a tiny highpass of it.
    color_shift = np.clip(np.median(pigment[core] - candidate[core], axis=0), -22, 22)
    threads = np.clip(candidate + color_shift[None, None, :], 0, 255)
    effective_alpha = np.clip(alpha * .99, 0, 1)
    final = stock * (1 - effective_alpha[..., None]) + threads * effective_alpha[..., None]
    if material.contact_width_px:
        # v4 only: preserve the actual twill, adding model-informed contact
        # shading within two pixels beside the exact source glyph silhouette.
        # The upper bound prevents an embossed outline or synthetic groove.
        visible = alpha >= .50
        distance = cv2.distanceTransform(np.uint8(~visible), cv2.DIST_L2, 3)
        editable_contact = np.asarray(material.edit_mask, dtype=np.uint8) > 0
        field = np.zeros(visible.shape, bool)
        left, top, right, bottom = material.guide_box
        field[top:bottom, left:right] = True
        contact = (~visible) & (distance > 0) & (
            distance <= material.contact_width_px + .25) & editable_contact & field
        if np.any(contact):
            model_occlusion = np.clip(np.mean(stock - candidate, axis=2), 0, 9)
            fade = np.clip(
                (material.contact_width_px + .25 - distance) /
                material.contact_width_px, 0, 1)
            depression = np.minimum(
                8.0, (.8 + .56 * model_occlusion) * fade)
            final[contact] = np.maximum(
                0, stock[contact] - depression[contact, None])
    result = np.uint8(np.clip(np.rint(final), 0, 255))
    # Immutable protection: no changed stock or factory branding outside edit mask.
    editable = np.asarray(material.edit_mask, dtype=np.uint8) > 0
    original = np.asarray(material.stock, dtype=np.uint8)
    result[~editable] = original[~editable]
    if not np.array_equal(result[~editable], original[~editable]):
        raise Slot1EmbroideryQAError("Image edit modified protected stock pixels.")
    final_luma = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY).astype(np.float32)
    final_high = final_luma - cv2.GaussianBlur(final_luma, (0, 0), 1.15)
    if float(np.std(final_high[core])) < 3.0:
        raise Slot1EmbroideryQAError("Final embroidery lacks photographic thread detail.")
    return _png(Image.fromarray(result, "RGB"))
