"""Single-pass Codex CLI stock-angle edit: yarn-only final raster, no patch masks.

Slot 2/3 use the original official stock as the ImageGen edit target. A saved
Slot 1 embroidery photograph is the stitch/color authority. Codex gets ONE
edit request; the deterministic output guard retains original factory pixels
and harvests only plausible thread colors from the native candidate.

No rectangular inpaint guide is sent to the model and no generated hat/photo
is published. Side view fails safely to the untouched stock when no credible
front-edge stitches remain visible. Three-quarter fails closed for QA review.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.realistic_review_ugc.threadtransfer import _layer_from_reference
from app.modules.realistic_review_ugc.slot1_embroidery import Slot1EmbroideryQAError
from app.modules.realistic_review_ugc.embroidery_scale import (
    EmbroideryScaleError, angle_scale_prompt, pinned_master, projected_angle_box,
)
from app.modules.realistic_review_ugc.three_quarter_placement import (
    ThreeQuarterPlacementInvalid, validate_three_quarter_corners,
)


class AngleDirectEditError(ValueError):
    pass


@dataclass(frozen=True)
class AngleDirectResult:
    output_png: bytes
    fallback_stock: bool
    fallback_reason: str | None
    native_sha256: str
    accepted_pixels: int
    edit_box: tuple[int, int, int, int] | None


# Manufacturer stock front-crown bounds only. The true side's FRONT edge is
# toward the visor (left on the factory right-side picture), NOT beside the
# existing GatorHats side logo on the rear half of the crown.
_FRONT_CROWN = {
    # The factory 3/4 front panel extends down close to the visor. The old
    # y1=.56 clipped the lower line of two-line embroidery and could reject a
    # genuine native edit as "no credible front embroidery". Leave the true
    # lateral panel and its GatorHats branding entirely outside the field.
    "prod_s2_ec2": (0.20, 0.24, 0.76, 0.65),
    "prod_s2_ec3": (0.22, 0.28, 0.46, 0.54),
}
# Side-front is a distinct physical camera view, not a clone of Slot 2.
# The wider eligible front-panel region is used only when verified side-front
# stock was pinned. A true 90-degree side retains the tiny visor-side sliver.
_SIDE_FRONT_VISIBLE_CROWN = (0.18, 0.24, 0.72, 0.65)
_MAX_PIXELS = 8_000_000
_MAX_BYTES = 25_000_000


def _verified_guide(guide: dict) -> list[list[float]]:
    if not isinstance(guide, dict) or guide.get("version") != "3q-free-v1":
        raise AngleDirectEditError("Missing pinned 3/4 freehand guide version.")
    try:
        return validate_three_quarter_corners(guide["corners"])
    except (KeyError, TypeError, ThreeQuarterPlacementInvalid) as exc:
        raise AngleDirectEditError("Invalid saved 3/4 freehand guide.") from exc


def angle_direct_prompt(
    stage: str, *, placement: dict | None = None,
    guide: dict | None = None, stock_role: str | None = None,
    autonomous: bool = False,
) -> str:
    """Single Codex image edit, with optional saved freehand Blueprint evidence."""
    common = (
        "ONE IMAGE-EDIT invocation only. The TARGET image (person) is the official "
        "unaltered factory cap stock photo for this exact angle and color. "
        "REFERENCE B is the SAVED photographic Embroidery Reference from Slot 1. "
        "It is the ONLY reference image: copy its exact lettering, spacing, "
        "line breaks, colors and real stitch texture, not its fabric background. "
        "NO flat artwork PNG or third reference is provided or needed. "
        "EDIT THE TARGET PHOTO; do not generate a replacement hat. Preserve the "
        "camera, crown and visor silhouette, seams, eyelets, hardware, original "
        "GatorHats side logo, twill weave, shadow, background and framing. "
        "Match B's exact letters, line breaks, original thread color, satin "
        "stitch direction and low-profile thread relief. No embroidery patch, "
        "rectangle, printed transfer, rubber/foam effect, background panel, "
        "new wordmark, extra text or altered GatorHats branding. "
        "No mask image, no intermediate output, no second inpaint. "
        "Output exactly ONE final edited PNG with the source aspect ratio. "
    )
    if autonomous:
        if placement is None or guide is None:
            raise AngleDirectEditError("Codex angle inference needs the pinned Slot 1 master and 3/4 guide.")
        points = _verified_guide(guide)
        front_box = pinned_master(placement)["front_box"]
        relative_width = (front_box[2] - front_box[0]) * 100
        relative_height = (front_box[3] - front_box[1]) * 100
        if stage == "prod_s2_ec2":
            coords = "; ".join(
                f"{name}=({x*100:.1f}% x, {y*100:.1f}% y)"
                for name, (x, y) in zip(
                    ("1 top-left", "2 top-right", "3 bottom-right", "4 bottom-left"),
                    points,
                )
            )
            common += (
                "USER-SAVED FREE FOUR-POINT 3/4 FRONT BLUEPRINT on THIS exact "
                f"stock angle: {coords}. The user can drag these corners anywhere; "
                "they are the editable front-embroidery field, NOT a fixed crown mask. "
                "Analyze the actual photographed front panel, its visible seams, "
                "fabric curvature, visor contact, light and perspective yourself. "
                "Fit the complete embroidery naturally WITHIN this user-shaped "
                "quadrilateral, with a small seam margin. The field can be skewed "
                "or trapezoidal; never draw a guide, colored points, mask, patch "
                "or rectangular background. Keep the physical stitch scale "
                f"relative to the saved FRONT embroidery ({relative_width:.1f}% "
                f"of full front-photo width x {relative_height:.1f}% height), "
                "adapting apparent width for the camera angle. Do NOT substitute "
                "the old fixed-center 3/4 formula or forcibly center or flatten "
                "the finished lettering. Use the user-provided guide as layout "
                "evidence and your visual reasoning for realistic surface fit. "
            )
        elif stage == "prod_s2_ec3":
            common += (
                "VIEW INFERENCE: This is a DIFFERENT original factory photograph "
                "from Slot 2. The saved 3/4 four-point guide establishes the "
                "same source design and FRONT physical scale but is NOT a "
                "coordinate map for the side/side-front picture. Inspect the "
                "side camera and reason which portion of the FRONT embroidery "
                "is genuinely visible. Match the reference and geometry, never "
                "paste the Slot 2 composition onto the side panel. "
            )
            if stock_role == "stock_side_front":
                common += (
                    "This is independently verified SIDE-FRONT stock with more "
                    "of the FRONT crown potentially visible: show ONLY the portion "
                    "actually facing the camera, possibly a partial readable "
                    "design, respecting the curved panel. It must remain a "
                    "distinct side-front photograph, not a duplicate of Slot 2. "
                )
        else:
            raise AngleDirectEditError("Unsupported Codex inference stage.")
    if stage == "prod_s2_ec2":
        return common + (angle_scale_prompt(placement, stage) + " " if placement and not autonomous else "") + (
            "This is a TRUE FRONT THREE-QUARTER view. Sew the complete reference "
            "typography onto the visible FRONT crown only. Apply natural "
            "foreshortening across the curved fabric and keep the design large "
            "enough to match the front-view reference, without enlarging past "
            "crown seams. The right/lateral side remains empty except for the "
            "existing factory GatorHats logo. If the edit would redraw the cap, "
            "leave protected areas identical and modify only the front yarn."
        )
    if stage == "prod_s2_ec3":
        return common + (angle_scale_prompt(placement, stage) + " " if placement and not autonomous else "") + (
            ("This is a SIDE-FRONT photographic view, with a partially visible front crown. "
             if stock_role == "stock_side_front" and autonomous else
             "This is a TRUE RIGHT SIDE 90-DEGREE view, visor points LEFT. ")
            + ("Show the naturally visible portion of the FRONT embroidery; "
             "do not invent a second slogan on the SIDE panel. "
             if stock_role == "stock_side_front" and autonomous else
             "The front panel is almost entirely hidden. Show, at most, a TINY "
             "physically visible edge of the original front embroidery on the "
             "LEFT/visor-facing crown edge. The side panel must NEVER display "
             "a complete or readable slogan, floating letters, copied glyphs "
             "near the lateral seams or underneath/above the side GatorHats logo. ")
            + "If no front embroidery should be visible, leave the target stock "
            "PHOTO UNCHANGED. Never move any letters toward the rear/right."
        )
    raise AngleDirectEditError("Unsupported stock angle for direct image edit.")


def _read_rgb(content: bytes, *, photo: bool) -> np.ndarray:
    if not content or len(content) > _MAX_BYTES:
        raise AngleDirectEditError("Direct stock edit image is missing or oversized.")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise AngleDirectEditError("Expected PNG, JPEG or WebP stock image.")
            width, height = source.size
            if min(width, height) < 512 or width * height > _MAX_PIXELS:
                raise AngleDirectEditError("Stock image dimensions are unsupported.")
            source.load()
            im = ImageOps.exif_transpose(source).convert("RGB")
            return np.asarray(im, dtype=np.uint8)
    except (OSError, Image.DecompressionBombError) as exc:
        raise AngleDirectEditError("Stock photo could not be decoded.") from exc


def _encode_png(rgb: np.ndarray) -> bytes:
    stream = BytesIO()
    Image.fromarray(rgb, "RGB").save(stream, "PNG", optimize=True)
    return stream.getvalue()


def _native_yarn_labs(reference_png: bytes) -> np.ndarray:
    try:
        layer, _ = _layer_from_reference(reference_png)
    except (Slot1EmbroideryQAError, OSError, ValueError) as exc:
        raise AngleDirectEditError("Pinned reference yarn cannot be isolated.") from exc
    rgba = np.asarray(layer.convert("RGBA"), dtype=np.uint8)
    pixels = rgba[:, :, :3][rgba[:, :, 3] > 220]
    if len(pixels) < 100:
        raise AngleDirectEditError("Pinned reference contains too little yarn.")
    # Stable selection, no random kmeans seed or nondeterministic CI behavior.
    sampled = pixels[::max(1, len(pixels) // 9000)]
    labs = cv2.cvtColor(sampled.reshape(1, -1, 3), cv2.COLOR_RGB2LAB)[0]
    hsv = cv2.cvtColor(sampled.reshape(1, -1, 3), cv2.COLOR_RGB2HSV)[0]
    # Do not merge unrelated pigments (e.g. orange ORGY + teal REFEREE) into
    # one brown median. Keep stable, bounded hue groups and brightness levels
    # for real yarn highlights, independent of source layout or thread color.
    values: list[np.ndarray] = []
    valid = hsv[:, 1] >= 40
    for bin_index in range(18):
        selected = valid & ((hsv[:, 0] // 10) == bin_index)
        if int(selected.sum()) < max(32, len(sampled) // 120):
            continue
        cluster = labs[selected].astype(np.float32)
        luminance = cluster[:, 0]
        values.append(np.median(cluster, axis=0))
        for boundary in (30, 70):
            threshold = np.percentile(luminance, boundary)
            band = cluster[luminance <= threshold] if boundary == 30 else cluster[luminance >= threshold]
            if len(band) >= 12:
                values.append(np.median(band, axis=0))
    # Preserve a fallback for muted/monochrome embroidery with no saturated
    # hue, while bounding the total CIELAB palette used during QA.
    if not values:
        luma = labs[:, 0]
        values = [np.median(labs, axis=0),
                  np.median(labs[luma <= np.percentile(luma, 33)], axis=0),
                  np.median(labs[luma >= np.percentile(luma, 67)], axis=0)]
    return np.float32(values[:56])


def _normalized_yarn_composite(
    stock: np.ndarray,
    native: np.ndarray,
    stitch_mask: np.ndarray,
    expected: tuple[int, int, int, int],
    *,
    stage: str,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Resize/recenter ONLY already-generated yarn pixels at the master scale.

    No interpolation of background, no rectangular inpaint: unselected pixels
    are copied verbatim from official stock. Uniform scale prevents stretching
    glyphs; extreme corrections fail (side uses unchanged stock fallback).
    """
    ys, xs = np.where(stitch_mask > 0)
    if len(xs) < 60:
        raise AngleDirectEditError("No visible stitch pixels for calibrated placement.")
    src_left, src_top = int(xs.min()), int(ys.min())
    src_right, src_bottom = int(xs.max())+1, int(ys.max())+1
    sw, sh = src_right-src_left, src_bottom-src_top
    ex0, ey0, ex1, ey1 = expected
    ew, eh = ex1-ex0, ey1-ey0
    if min(sw, sh, ew, eh) < 8:
        raise AngleDirectEditError("Embroidery cannot fit its physical master box.")
    ratio = min(ew/sw, eh/sh)
    if not .46 <= ratio <= (2.2 if stage == "prod_s2_ec3" else 1.65):
        raise AngleDirectEditError("Native edit scale differs too much from Slot 1.")
    tw, th = max(8, round(sw*ratio)), max(8, round(sh*ratio))
    px = round((ex0+ex1-tw)/2)
    py = round((ey0+ey1-th)/2)
    # Projection happens only inside the calibrated front-facing stock field.
    width, height = stock.shape[1], stock.shape[0]
    fx0, fy0, fx1, fy1 = _FRONT_CROWN[stage]
    if not (0 <= px and 0 <= py and px+tw <= width and py+th <= height
            and px >= round(fx0*width) and py >= round(fy0*height)
            and px+tw <= round(fx1*width) and py+th <= round(fy1*height)):
        raise AngleDirectEditError("Calibrated yarn would cross a protected crown seam.")

    mask = stitch_mask[src_top:src_bottom, src_left:src_right].astype(np.float32) / 255.
    mask = cv2.GaussianBlur(mask, (0, 0), .55)
    rgb = native[src_top:src_bottom, src_left:src_right].astype(np.float32)
    premul = rgb * mask[..., None]
    interpolation = cv2.INTER_AREA if ratio < 1 else cv2.INTER_CUBIC
    output_alpha = cv2.resize(mask, (tw, th), interpolation=interpolation)
    output_premul = cv2.resize(premul, (tw, th), interpolation=interpolation)
    output_alpha = np.clip(output_alpha, 0, 1)
    yarn = np.divide(output_premul, np.maximum(output_alpha[..., None], .001))
    base = stock[py:py+th, px:px+tw].astype(np.float32)
    patch = yarn*output_alpha[..., None]+base*(1-output_alpha[..., None])
    result = stock.copy()
    result[py:py+th, px:px+tw] = np.uint8(np.clip(np.rint(patch), 0, 255))
    if (not np.array_equal(result[:py], stock[:py])
            or not np.array_equal(result[py+th:], stock[py+th:])
            or not np.array_equal(result[:, :px], stock[:, :px])
            or not np.array_equal(result[:, px+tw:], stock[:, px+tw:])):
        raise AngleDirectEditError("Stock pixels outside sewn yarn were modified.")
    center_x = (px+tw/2)/width
    center_y = (py+th/2)/height
    expected_center_x = (ex0+ex1)/2/width
    expected_center_y = (ey0+ey1)/2/height
    if abs(center_x-expected_center_x) > .04 or abs(center_y-expected_center_y) > .04:
        raise AngleDirectEditError("Embroidery center differs from the saved master placement.")
    return result, (px, py, px+tw, py+th)


def process_direct_angle_edit(
    original_stock: bytes,
    generated: GeneratedImageResult,
    embroidery_reference: bytes,
    *,
    stage: str,
    placement: dict | None = None,
    guide: dict | None = None,
    stock_role: str | None = None,
    autonomous: bool = False,
) -> AngleDirectResult:
    """QA and harvest thread pixels from a native one-pass edit.

    All result pixels outside verified yarn contours are taken from original
    manufacturer stock. No rectangular inpaint/color patch can survive.
    """
    if stage not in _FRONT_CROWN:
        raise AngleDirectEditError("Unknown direct stock angle.")
    stock = _read_rgb(original_stock, photo=True)
    native = _read_rgb(generated.image_bytes, photo=False)
    digest = sha256(generated.image_bytes).hexdigest()
    provenance = (generated.provider_metadata or {}).get("native_sha256")
    if provenance is not None and provenance != digest:
        raise AngleDirectEditError("Native Codex edit provenance does not match output bytes.")
    if stock.shape != native.shape:
        # ImageGen may return its native 1024/1536 output size. Only the
        # candidate embroidery is aligned to stock; the final original stock
        # pixels and camera crop are never resampled.
        aspect_stock = stock.shape[1] / stock.shape[0]
        aspect_native = native.shape[1] / native.shape[0]
        if abs(aspect_stock - aspect_native) > .012:
            raise AngleDirectEditError("Codex altered stock frame dimensions.")
        native = cv2.resize(native, (stock.shape[1], stock.shape[0]),
                            interpolation=cv2.INTER_CUBIC)
    height, width = stock.shape[:2]
    if autonomous and (guide is None or placement is None):
        raise AngleDirectEditError("Codex angle edit is missing the pinned master or freehand guide.")
    if autonomous and stage == "prod_s2_ec2":
        # The true saved four-point polygon replaces all fixed 3/4 bounds.
        # Work in original stock pixels; no new rectangular mask goes to Codex.
        points = _verified_guide(guide)
        polygon = np.array([
            (round(x * (width - 1)), round(y * (height - 1))) for x, y in points
        ], dtype=np.int32)
        roi = np.zeros((height, width), np.uint8)
        cv2.fillConvexPoly(roi, polygon, 255)
        x0, y0 = np.min(polygon, axis=0).tolist()
        x1, y1 = (np.max(polygon, axis=0) + 1).tolist()
    else:
        if autonomous:
            # Slot 3's photo has its OWN projection. Never map Slot 2's
            # freehand points onto this different camera angle.
            _verified_guide(guide)
        zone = (_SIDE_FRONT_VISIBLE_CROWN
                if stage == "prod_s2_ec3" and stock_role == "stock_side_front" and autonomous
                else _FRONT_CROWN[stage])
        x0, y0, x1, y1 = (
            round(width * zone[0]), round(height * zone[1]),
            round(width * zone[2]), round(height * zone[3]),
        )
        roi = np.zeros((height, width), np.uint8)
        roi[y0:y1, x0:x1] = 255
    orig_lab = cv2.cvtColor(stock, cv2.COLOR_RGB2LAB).astype(np.float32)
    new_lab = cv2.cvtColor(native, cv2.COLOR_RGB2LAB).astype(np.float32)
    yarn_colors = _native_yarn_labs(embroidery_reference)
    diff = np.linalg.norm(new_lab - orig_lab, axis=2)
    color_distance = np.min(
        np.stack([np.linalg.norm(new_lab - color, axis=2)
                  for color in yarn_colors], axis=0), axis=0,
    )
    # Only actual new thread-pigment pixels, never similar beige/cream fabric.
    foreground = (roi > 0) & (diff > 23) & (color_distance < 47)
    raw = np.uint8(foreground) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(raw, 8)
    cleaned = np.zeros_like(raw)
    component_min = max(6, round(width * height * .000007))
    filled_patch = False
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < component_min:
            continue
        bw = max(1, int(stats[i, cv2.CC_STAT_WIDTH]))
        bh = max(1, int(stats[i, cv2.CC_STAT_HEIGHT]))
        # A broad solid rectangle masquerading as an embroidery patch should
        # never be treated as a single glyph, even when it is thread-colored.
        if area >= max(300, round((x1-x0)*(y1-y0)*.11)) and area / (bw*bh) > .91:
            filled_patch = True
        cleaned[labels == i] = 255
    pixels = int(np.count_nonzero(cleaned))
    safe_min = (max(160, round(width * height * .00012))
                if stage == "prod_s2_ec2" else max(60, round(width * height * .00004)))
    roi_area = max(1, int(np.count_nonzero(roi)))
    reason: str | None = None
    if filled_patch or pixels > roi_area * .31:
        reason = "Edited area resembles a filled patch rather than individual stitches."
    elif pixels < safe_min:
        reason = "No credible front embroidery detected inside the visible front crown."
    else:
        ys, xs = np.where(cleaned > 0)
        target = native[min(ys):max(ys)+1, min(xs):max(xs)+1]
        mask = cleaned[min(ys):max(ys)+1, min(xs):max(xs)+1]
        if (stage == "prod_s2_ec3" and not (
                autonomous and stock_role == "stock_side_front")
                and (max(xs)-min(xs)+1) > (x1-x0)*.42):
            reason = "Side view exposes a full slogan instead of a front-edge sliver."
        interior = cv2.erode(mask, np.ones((3, 3), np.uint8)) > 0
        # Reject uniform printed pigment when there is enough stitch interior.
        if reason is None and int(np.count_nonzero(interior)) > 120:
            gray = cv2.cvtColor(target, cv2.COLOR_RGB2GRAY).astype(np.float32)
            high = gray - cv2.GaussianBlur(gray, (0, 0), 1.2)
            if float(np.std(high[interior])) < 1.05:
                reason = "Edited lettering is too flat to verify yarn texture."
    if reason:
        if stage != "prod_s2_ec3" or (autonomous and stock_role == "stock_side_front"):
            raise AngleDirectEditError(reason)
        # Correct geometry is more important than inventing side lettering.
        return AngleDirectResult(
            _encode_png(stock), True, reason, digest, 0, None,
        )
    if autonomous:
        # Codex owns exact letter projection and positioning. Never normalize
        # its already sewn stitches into a fixed centered rectangle: doing so
        # flattened the perspective and ignored the saved freehand 3/4 guide.
        master = pinned_master(placement)
        ys, xs = np.where(cleaned > 0)
        actual_width = (int(xs.max()) - int(xs.min()) + 1) / width
        actual_height = (int(ys.max()) - int(ys.min()) + 1) / height
        if stage == "prod_s2_ec2":
            front_box = master["front_box"]
            front_width = front_box[2] - front_box[0]
            front_height = front_box[3] - front_box[1]
            # Validate plausible physical size, not a preset pixel position.
            guide_width = (x1 - x0) / width
            guide_height = (y1 - y0) / height
            if (actual_width < min(guide_width * .30, front_width * .28)
                    or actual_height < min(guide_height * .18, front_height * .27)):
                raise AngleDirectEditError(
                    "Codex embroidery is too small for the saved FRONT scale and 3/4 guide."
                )
        # Side-front must genuinely show thread rather than a blank success.
        # True side-90 may instead legitimately return unchanged stock above.
    elif placement is not None:
        # Older pinned v3 edits retain their original master-box normalization.
        # Never silently change an already running/completed job.
        try:
            expected = projected_angle_box(placement, stage, (width, height))
            output, actual = _normalized_yarn_composite(
                stock, native, cleaned, expected, stage=stage,
            )
        except (AngleDirectEditError, EmbroideryScaleError) as exc:
            if stage != "prod_s2_ec3":
                raise AngleDirectEditError(str(exc)) from exc
            return AngleDirectResult(
                _encode_png(stock), True, str(exc), digest, 0, None,
            )
        return AngleDirectResult(_encode_png(output), False, None, digest,
                                 pixels, actual)
    # Convert the handoff to a *stitch-shaped* mask instead of a rectangular
    # box. Feather is at most one pixel, protecting all other factory pixels.
    alpha = cv2.GaussianBlur(cleaned.astype(np.float32)/255., (0,0), .55)
    alpha[roi == 0] = 0
    output = np.uint8(np.clip(
        np.rint(native.astype(np.float32)*alpha[..., None]
                + stock.astype(np.float32)*(1. - alpha[..., None])),
        0, 255,
    ))
    if not np.array_equal(output[roi == 0], stock[roi == 0]):
        raise AngleDirectEditError("Protected official stock pixels were modified.")
    ys, xs = np.where(cleaned > 0)
    return AngleDirectResult(
        _encode_png(output), False, None, digest, pixels,
        (int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)),
    )
