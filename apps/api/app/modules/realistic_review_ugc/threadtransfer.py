"""ThreadTransfer V1: pixel-local embroidery reference transfer onto factory cap.

This is deliberately a deterministic raster compositor, NOT a generative
photograph replacement. Existing photographic Embroidery References are
preserved as independent historical assets. A fresh reference is needed only
for a design with no verified, pinned reference. Reject uncertain segmentation
instead of publishing a fabric rectangle or printed-looking fake.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.modules.realistic_review_ugc.slot1_embroidery import (
    Slot1EmbroideryQAError, Slot1MaterialPass,
)

_MAX_REFERENCE_PIXELS = 8_000_000


@dataclass(frozen=True)
class ThreadTransferResult:
    output_png: bytes
    thread_layer_sha256: str
    extracted_pixels: int
    target_box: tuple[int, int, int, int]


def _layer_from_reference(image_bytes: bytes) -> tuple[Image.Image, int]:
    """Extract real yarn pixels; never bring the photographic backing cloth.

    Transparent masters use supplied alpha. Opaque photos are conservatively
    keyed against *their own border* in CIELAB, retaining saturated and dark
    yarn while excluding low-contrast beige twill. Ambiguous references fail.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            if opened.format not in {"PNG", "WEBP"}:
                raise Slot1EmbroideryQAError("Thread reference must be PNG or WebP.")
            if min(opened.size) < 256 or opened.width * opened.height > _MAX_REFERENCE_PIXELS:
                raise Slot1EmbroideryQAError("Thread reference dimensions are invalid.")
            opened.load()
            rgba = np.asarray(ImageOps.exif_transpose(opened).convert("RGBA"), dtype=np.uint8)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise Slot1EmbroideryQAError("Thread reference cannot be decoded.") from exc

    h, w = rgba.shape[:2]
    native_alpha = rgba[:, :, 3]
    border = max(5, round(min(h, w) * .035))
    edge_alpha = np.concatenate((
        native_alpha[:border].ravel(), native_alpha[-border:].ravel(),
        native_alpha[:, :border].ravel(), native_alpha[:, -border:].ravel(),
    ))
    if np.median(edge_alpha) < 100 and np.count_nonzero(native_alpha) >= 160:
        # A real alpha master is an authority: no color-key estimation.
        mask = native_alpha.copy()
    else:
        rgb = rgba[:, :, :3]
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        edges_lab = np.concatenate((
            lab[:border].reshape(-1, 3), lab[-border:].reshape(-1, 3),
            lab[:, :border].reshape(-1, 3), lab[:, -border:].reshape(-1, 3),
        ))
        edges_sat = np.concatenate((
            hsv[:border, :, 1].ravel(), hsv[-border:, :, 1].ravel(),
            hsv[:, :border, 1].ravel(), hsv[:, -border:, 1].ravel(),
        ))
        cloth_lab = np.median(edges_lab, axis=0)
        cloth_sat = float(np.median(edges_sat))
        distance = np.linalg.norm(lab - cloth_lab[None, None, :], axis=2)
        saturation = hsv[:, :, 1].astype(np.float32)
        luminance_delta = np.abs(lab[:, :, 0] - cloth_lab[0])
        foreground = (
            (distance > 31)
            | ((distance > 20) & (saturation > max(55, cloth_sat + 42)))
            | ((distance > 23) & (luminance_delta > 23))
        ) & (native_alpha > 20)
        binary = np.uint8(foreground) * 255
        # Suppress standalone weave flecks while retaining narrow lettering.
        n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        minimum = max(8, round(h * w * .000008))
        keep = np.zeros(binary.shape, np.uint8)
        for i in range(1, n):
            if int(stats[i, cv2.CC_STAT_AREA]) >= minimum:
                keep[labels == i] = 255
        # A tight 1px feather prevents fabric-colored halos around yarn edges.
        mask = np.uint8(np.clip(
            cv2.GaussianBlur(keep.astype(np.float32), (0, 0), .55) * 1.08,
            0, 255,
        ))
    visible = mask >= 55
    count = int(np.count_nonzero(visible))
    if count < max(180, round(w * h * .004)) or count > w * h * .72:
        raise Slot1EmbroideryQAError(
            "Reference cloth cannot be separated safely from its embroidery.")
    ys, xs = np.where(visible)
    left = max(0, int(xs.min()) - 2)
    right = min(w, int(xs.max()) + 3)
    top = max(0, int(ys.min()) - 2)
    bottom = min(h, int(ys.max()) + 3)
    raw = rgba[top:bottom, left:right].copy()
    raw[:, :, 3] = mask[top:bottom, left:right]
    # Reject completely flat print as a thread material input. Check the
    # original reference, not the fabric backdrop and not binary mask edges.
    core = cv2.erode(np.uint8(raw[:, :, 3] > 240), np.ones((3, 3), np.uint8)) > 0
    if int(np.count_nonzero(core)) >= 500:
        gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
        high = gray - cv2.GaussianBlur(gray, (0, 0), 1.2)
        if float(np.std(high[core])) < 1.1:
            raise Slot1EmbroideryQAError("Reference lacks visible thread texture.")
    return Image.fromarray(raw, "RGBA"), count


def composite_reference_on_cap(
    material: Slot1MaterialPass, reference_png: bytes, *,
    design_size: int = 100, scale_contract: dict | None = None,
) -> ThreadTransferResult:
    """Blend only extracted thread against immutable original factory pixels.

    Perspective-aware shallow warp maps the embroidery over the convex front
    crown. Bounds remain inside the user's pinned embroidery placement box.
    Pixels outside the box are byte-identical to the original stock RGB.
    """
    import hashlib

    layer, extracted = _layer_from_reference(reference_png)
    if scale_contract is not None:
        # New front photos use an explicit physical master size; do not infer
        # size from a generated macro's margins or legacy field height.
        from app.modules.realistic_review_ugc.embroidery_scale import (
            EmbroideryScaleError, front_transfer_box,
        )
        try:
            px, py, rx, by = front_transfer_box(
                scale_contract, layer.width / layer.height, material.stock.size,
            )
        except EmbroideryScaleError as exc:
            raise Slot1EmbroideryQAError(str(exc)) from exc
        tw, th = rx-px, by-py
        safe = scale_contract["safe_box"]
        x0, y0, x1, y1 = (
            round(material.stock.width * safe[0]),
            round(material.stock.height * safe[1]),
            round(material.stock.width * safe[2]),
            round(material.stock.height * safe[3]),
        )
    else:
        # Existing queued/cached Slot 1 jobs keep historical pixel placement.
        x0, y0, x1, y1 = material.guide_box
        field_w, field_h = x1 - x0, y1 - y0
        if field_w < 48 or field_h < 32 or layer.width < 12 or layer.height < 12:
            raise Slot1EmbroideryQAError("Reference or embroidery area is too small.")
        zoom = min(2.0, max(.4, design_size / 100.))
        scale = min(field_w * .95 / layer.width, field_h * .92 / layer.height) * zoom
        tw = min(field_w - 2, max(8, round(layer.width * scale)))
        th = min(field_h - 2, max(8, round(layer.height * scale)))
        px = x0 + (field_w - tw) // 2
        py = y0 + (field_h - th) // 2
    if tw < 12 or th < 10:
        raise Slot1EmbroideryQAError("Embroidery placement is too small.")
    target = np.asarray(layer.resize((tw, th), Image.Resampling.LANCZOS), dtype=np.float32)
    # Remap *premultiplied* pixels so beige reference cloth cannot bleed at
    # semi-transparent edges. Curvature is bounded to the crown field.
    xx, yy = np.meshgrid(np.arange(tw, dtype=np.float32),
                         np.arange(th, dtype=np.float32))
    center = (tw - 1) * .5
    nx = (xx - center) / max(center, 1.)
    map_x = center + (xx - center) * (1.0 + .018 * nx * nx)
    map_y = yy - .022 * th * nx * nx
    alpha = np.clip(target[:, :, 3:4] / 255., 0, 1)
    premul = target[:, :, :3] * alpha
    warped_alpha = cv2.remap(alpha[:, :, 0], map_x, map_y, cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    warped_rgb = cv2.remap(premul, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    yarn = np.divide(warped_rgb, np.maximum(warped_alpha[..., None], .001))
    if int(np.count_nonzero(warped_alpha > .5)) < 140:
        raise Slot1EmbroideryQAError("Thread transfer lost the embroidered glyphs.")

    stock = np.asarray(material.stock.convert("RGB"), dtype=np.uint8)
    result = stock.copy()
    backing = stock[py:py + th, px:px + tw].astype(np.float32)
    # Respect local crown brightness without repainting the hat or shifting
    # yarn hue. This remains a modest photographic adaptation (max 6%).
    blurred_luma = cv2.GaussianBlur(
        cv2.cvtColor(np.uint8(backing), cv2.COLOR_RGB2GRAY).astype(np.float32),
        (0, 0), max(3, min(tw, th) * .04),
    )
    exposure = np.clip((blurred_luma - np.median(blurred_luma)) / 255., -.06, .06)
    yarn = np.clip(yarn * (1. + exposure[..., None]), 0, 255)
    a = warped_alpha[..., None]
    rgb = yarn * a + backing * (1. - a)
    # The stitch-foot contact shadow is confined to a <= 2px neighborhood.
    # No broad stroke outline, decal border or fabric rectangle.
    halo = np.maximum(
        cv2.GaussianBlur(warped_alpha, (0, 0), 1.) - warped_alpha, 0,
    )
    rgb *= (1. - (halo * .05)[..., None])
    result[py:py + th, px:px + tw] = np.uint8(np.clip(np.rint(rgb), 0, 255))
    if (not np.array_equal(result[:y0], stock[:y0])
            or not np.array_equal(result[y1:], stock[y1:])
            or not np.array_equal(result[:, :x0], stock[:, :x0])
            or not np.array_equal(result[:, x1:], stock[:, x1:])):
        raise Slot1EmbroideryQAError("Transfer modified protected factory stock.")
    output = BytesIO()
    Image.fromarray(result, "RGB").save(output, "PNG", optimize=True)
    layer_out = BytesIO()
    layer.save(layer_out, "PNG", optimize=True)
    return ThreadTransferResult(
        output_png=output.getvalue(),
        thread_layer_sha256=hashlib.sha256(layer_out.getvalue()).hexdigest(),
        extracted_pixels=extracted,
        target_box=(px, py, px + tw, py + th),
    )
