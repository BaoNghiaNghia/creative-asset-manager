"""Products Stage 2 ThreadTransfer V2: local official-stock angle projection.

The saved Embroidery Reference is the sole yarn authority. No new cap,
lettering, brand or side logos are generated. The target is an immutable,
pinned official stock photograph, and only a small, angle-specific FRONT
crown patch is editable. Zero model calls; ambiguity fails closed.
"""
from __future__ import annotations

import hashlib
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.modules.realistic_review_ugc.slot1_embroidery import Slot1EmbroideryQAError
from app.modules.realistic_review_ugc.threadtransfer import (
    ThreadTransferResult, _layer_from_reference,
)

# Conservative placement on the manufacturer stock. Measured within the
# pre-existing stock-angle protection fields in stage2_stock_inpaint.py.
# Side shows only the sliver of the FRONT edge, never lateral GatorHats logo.
_VIEW_CROWN = {
    "prod_s2_ec2": (.265, .335, .755, .53),
    "prod_s2_ec3": (.685, .355, .760, .515),
}
_MAX_PIXELS = 8_000_000


def _composite_pinned_view(
    original: np.ndarray,
    layer: Image.Image,
    stage: str,
    master: dict,
    *,
    side_front_fallback: bool = False,
    guide: dict | None = None,
) -> ThreadTransferResult:
    """V3: perspective-warp SAME real yarn with one predefined stock quad.

    Not a native image edit. Never generates or moves stock fabric, visor,
    seams, background or manufacturer side lettering.
    """
    from app.modules.realistic_review_ugc.embroidery_scale import (
        EmbroideryScaleError, projected_raster_box,
    )

    height, width = original.shape[:2]
    saved_quad: np.ndarray | None = None
    if guide is not None:
        # Slot 2 only: the saved, freely positioned 3/4 quadrilateral is the
        # authority. A template crown box must not clip or recenter its yarn.
        from app.modules.realistic_review_ugc.three_quarter_placement import (
            ThreeQuarterPlacementInvalid, validate_three_quarter_corners,
        )
        if stage != "prod_s2_ec2" or not isinstance(guide, dict) or guide.get("version") != "3q-free-v1":
            raise Slot1EmbroideryQAError("Invalid saved 3/4 embroidery guide.")
        try:
            corners = validate_three_quarter_corners(guide["corners"])
        except (KeyError, TypeError, ThreeQuarterPlacementInvalid) as exc:
            raise Slot1EmbroideryQAError("Invalid saved 3/4 embroidery guide.") from exc
        raw_quad = np.float32([(x*(width-1), y*(height-1)) for x, y in corners])
        center = raw_quad.mean(axis=0)
        # A 2% inset leaves a small seam margin without introducing any
        # rectangular stamp or modifying pixels outside the saved field.
        saved_quad = center + (raw_quad - center)*.96
        x0, y0 = np.floor(saved_quad.min(axis=0)).astype(int)
        x1, y1 = np.ceil(saved_quad.max(axis=0)).astype(int) + 1
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(width, int(x1)), min(height, int(y1))
        if x1-x0 < 12 or y1-y0 < 12:
            raise Slot1EmbroideryQAError("Saved 3/4 embroidery field is too small.")
    else:
        try:
            x0, y0, x1, y1 = projected_raster_box(
                master, stage, (width, height),
                side_front_fallback=side_front_fallback,
            )
        except EmbroideryScaleError as exc:
            raise Slot1EmbroideryQAError(str(exc)) from exc
    rgba = np.asarray(layer.convert("RGBA"), dtype=np.uint8)
    if stage == "prod_s2_ec3" and not side_front_fallback:
        # Historical side-90 jobs physically expose only the front-edge
        # sliver. Fresh jobs with the front-visible fallback use full yarn.
        rgba = rgba[:, max(0, round(rgba.shape[1] * .82)):, :].copy()
    src_h, src_w = rgba.shape[:2]
    if min(src_w, src_h) < 10:
        raise Slot1EmbroideryQAError("Saved thread layer has insufficient visible detail.")
    if saved_quad is not None:
        # The four handles define the available seam-safe field, not a
        # command to stretch lettering. Keep the reference glyph proportions
        # with the natural ~12% foreshortening of a 3/4 camera angle.
        top_w = np.linalg.norm(saved_quad[1] - saved_quad[0])
        bottom_w = np.linalg.norm(saved_quad[2] - saved_quad[3])
        left_h = np.linalg.norm(saved_quad[3] - saved_quad[0])
        right_h = np.linalg.norm(saved_quad[2] - saved_quad[1])
        field_aspect = (top_w + bottom_w) / max(left_h + right_h, 1.)
        yarn_aspect = (src_w / src_h) * .88
        sx = min(1., yarn_aspect / max(field_aspect, .001))
        sy = min(1., field_aspect / max(yarn_aspect, .001))
        center = saved_quad.mean(axis=0)
        saved_quad = center + (saved_quad - center)*np.float32([sx, sy])
        x0, y0 = np.floor(saved_quad.min(axis=0)).astype(int)
        x1, y1 = np.ceil(saved_quad.max(axis=0)).astype(int) + 1
    tw, th = x1-x0, y1-y0
    if min(tw, th) < 12:
        raise Slot1EmbroideryQAError("Calibrated 3/4 embroidery is too small.")
    if saved_quad is not None:
        quad = saved_quad
    elif stage == "prod_s2_ec2" or side_front_fallback:
        quad = np.float32([
            [x0+tw*.055, y0+th*.025],
            [x1-tw*.085, y0+th*.095],
            [x1-tw*.018, y1-th*.065],
            [x0+tw*.015, y1-th*.025],
        ])
    else:
        quad = np.float32([
            [x0+tw*.080, y0+th*.025],
            [x1-tw*.095, y0+th*.125],
            [x1-tw*.045, y1-th*.050],
            [x0+tw*.030, y1-th*.020],
        ])
    # Clip the raster operation to a tiny local front crown buffer.
    pad = 3
    left, top = max(0, x0-pad), max(0, y0-pad)
    right, bottom = min(width, x1+pad), min(height, y1+pad)
    fw, fh = right-left, bottom-top
    src = np.float32([
        [0, 0], [src_w-1, 0], [src_w-1, src_h-1], [0, src_h-1],
    ])
    matrix = cv2.getPerspectiveTransform(
        src, quad - np.float32([left, top]),
    )
    alpha_src = rgba[:, :, 3].astype(np.float32)/255.
    premul_src = rgba[:, :, :3].astype(np.float32)*alpha_src[:, :, None]
    alpha = np.clip(cv2.warpPerspective(
        alpha_src, matrix, (fw, fh), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    ), 0, 1)
    premul = cv2.warpPerspective(
        premul_src, matrix, (fw, fh), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    selected = alpha > .5
    accepted = int(np.count_nonzero(selected))
    if accepted < (100 if stage == "prod_s2_ec2" or side_front_fallback else 35):
        raise Slot1EmbroideryQAError("Embroidery reference contains no visible yarn at this angle.")
    ys, xs = np.where(selected)
    # Ensure the warp actually occupies its calibrated target, rather than
    # silently publishing a microscopic or clipped glyph.
    visible_w = int(xs.max()-xs.min()+1)
    if visible_w < tw*(.50 if stage == "prod_s2_ec2" or side_front_fallback else .28):
        raise Slot1EmbroideryQAError("Calibrated embroidery width is insufficient.")
    yarn = np.divide(premul, np.maximum(alpha[:, :, None], .001))
    background = original[top:bottom, left:right].astype(np.float32)
    # Gentle lighting integration acts on extracted yarn alone.
    light = cv2.GaussianBlur(
        cv2.cvtColor(np.uint8(background), cv2.COLOR_RGB2GRAY).astype(np.float32),
        (0, 0), max(2., min(fw, fh)*.06),
    )
    exposure = np.clip((light-np.median(light))/255., -.06, .06)
    yarn = np.clip(yarn*(1.+exposure[:, :, None]), 0, 255)
    composite = yarn*alpha[:, :, None]+background*(1.-alpha[:, :, None])
    # Narrow stitch-foot contact shadow, no embroidered fabric rectangle.
    fringe = np.maximum(cv2.GaussianBlur(alpha, (0, 0), .8)-alpha, 0)
    composite *= 1.-fringe[:, :, None]*.04
    result = original.copy()
    result[top:bottom, left:right] = np.uint8(np.clip(np.rint(composite), 0, 255))
    if (not np.array_equal(result[:top], original[:top])
            or not np.array_equal(result[bottom:], original[bottom:])
            or not np.array_equal(result[:, :left], original[:, :left])
            or not np.array_equal(result[:, right:], original[:, right:])):
        raise Slot1EmbroideryQAError("Local transfer modified protected factory stock.")
    # numpy scalar coordinates (np.int64) cannot be stored as JSONB when
    # this result is recorded in immutable Ecommerce output history.
    mask_box = (int(left) + int(xs.min()), int(top) + int(ys.min()),
                int(left) + int(xs.max()) + 1, int(top) + int(ys.max()) + 1)
    encoded = BytesIO()
    Image.fromarray(result, "RGB").save(encoded, "PNG", optimize=True)
    layer_png = BytesIO()
    layer.save(layer_png, "PNG", optimize=True)
    return ThreadTransferResult(
        output_png=encoded.getvalue(),
        thread_layer_sha256=hashlib.sha256(layer_png.getvalue()).hexdigest(),
        extracted_pixels=accepted,
        target_box=mask_box,
    )


def composite_reference_on_stock_view(
    stock_bytes: bytes,
    reference_png: bytes,
    *,
    stage: str,
    design_size: int = 100,
    placement: dict | None = None,
    side_front_fallback: bool = False,
    guide: dict | None = None,
) -> ThreadTransferResult:
    """Project yarn onto a real angle; protect every pixel outside target ROI.

    Image is modified in the tiny source-front-crown field only. 3/4 uses a
    perspective trapezoid; 90-degree side reveals just the edge of a glyph
    instead of stamping a full readable logo on the lateral panel.
    """
    if stage not in _VIEW_CROWN:
        raise Slot1EmbroideryQAError("ThreadTransfer stock view must be true 3/4 or right side.")
    try:
        with Image.open(BytesIO(stock_bytes)) as source:
            if (source.format not in ("JPEG", "PNG", "WEBP")
                    or min(source.size) < 512
                    or source.width * source.height > _MAX_PIXELS):
                raise Slot1EmbroideryQAError("Official stock photo format or size is invalid.")
            source.load()
            img = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise Slot1EmbroideryQAError("Official stock photo could not be decoded.") from exc

    layer, _count = _layer_from_reference(reference_png)
    original = np.asarray(img, dtype=np.uint8)
    if placement is not None:
        return _composite_pinned_view(
            original, layer, stage, placement,
            side_front_fallback=side_front_fallback, guide=guide,
        )
    if guide is not None:
        raise Slot1EmbroideryQAError("Saved 3/4 guide requires a pinned Slot 1 master.")
    if side_front_fallback:
        raise Slot1EmbroideryQAError("Front-visible fallback requires a saved Slot 1 master.")
    # Pre-V3 pinned work uses the historical stock-safe v2 fields.
    height, width = original.shape[:2]
    fx0, fy0, fx1, fy1 = _VIEW_CROWN[stage]
    left, top, right, bottom = (round(width * fx0), round(height * fy0),
                                round(width * fx1), round(height * fy1))
    field_w, field_h = right-left, bottom-top
    if min(field_w, field_h) < 26:
        raise Slot1EmbroideryQAError("Official stock front crown field is too small.")
    zoom = min(2., max(.4, float(design_size)/100.))
    rgba = np.asarray(layer, dtype=np.uint8)
    if stage == "prod_s2_ec3":
        # Only the farthest right 18% of the original front embroidery can
        # remain visible from a true 90-degree side orientation.
        # Preserve the original glyph crop, not a miniature whole slogan.
        rgba = rgba[:, max(0, int(rgba.shape[1]*.82)):, :]
    if rgba.shape[1] < 8:
        raise Slot1EmbroideryQAError("Reference is too narrow for side projection.")
    src_h, src_w = rgba.shape[:2]
    fit = min((field_w*.92)/src_w, (field_h*.9)/src_h)
    tw = min(field_w-2, max(8, round(src_w * fit * zoom)))
    th = min(field_h-2, max(8, round(src_h * fit * zoom)))
    if min(tw,th) < 12:
        raise Slot1EmbroideryQAError("Embroidery would become illegibly small.")
    x = left + (field_w-tw)//2
    y = top + (field_h-th)//2
    if stage == "prod_s2_ec2":
        # The right face recedes, foreshortening lettering on the curved panel.
        quad = np.float32([
            (x+tw*.06, y+th*.035),
            (x+tw*.91, y+th*.10),
            (x+tw*.97, y+th*.88),
            (x+tw*.0, y+th*.97),
        ])
    else:
        quad = np.float32([
            (x+tw*.05, y+th*.04),
            (x+tw*.88, y+th*.15),
            (x+tw*.93, y+th*.91),
            (x+tw*.02, y+th*.96),
        ])
    quad[:, 0] -= left
    quad[:, 1] -= top
    src_points = np.float32([(0,0),(src_w-1,0),(src_w-1,src_h-1),(0,src_h-1)])
    warp = cv2.getPerspectiveTransform(src_points, quad)
    alpha_src = rgba[:,:,3].astype(np.float32)/255.
    rgb_premul_src = rgba[:,:,:3].astype(np.float32)*alpha_src[...,None]
    mask = cv2.warpPerspective(alpha_src, warp, (field_w,field_h), flags=cv2.INTER_LINEAR)
    premul = cv2.warpPerspective(rgb_premul_src, warp, (field_w,field_h), flags=cv2.INTER_LINEAR)
    colored = np.divide(premul, np.maximum(mask[...,None], .001))
    if int(np.count_nonzero(mask > .5)) < 90:
        raise Slot1EmbroideryQAError("The angled view does not expose enough embroidery.")
    bg = original[top:bottom,left:right].astype(np.float32)
    # Only +/-6% physically motivated adjustment to local exposure.
    lum = cv2.cvtColor(bg.astype(np.uint8),cv2.COLOR_RGB2GRAY).astype(np.float32)
    illumination = cv2.GaussianBlur(lum,(0,0),max(3.,min(field_w,field_h)*.055))
    shade = np.clip((illumination-np.median(illumination))/255.,-.06,.06)
    colored = np.clip(colored*(1.+shade[...,None]),0,255)
    halo = np.maximum(cv2.GaussianBlur(mask,(0,0),1.)-mask,0)
    composite = colored*mask[...,None]+bg*(1.-mask[...,None])
    composite *= 1.-halo[...,None]*.05
    final = original.copy()
    final[top:bottom,left:right] = np.uint8(np.clip(np.rint(composite),0,255))
    # Assert no full photo generative change, no edge or GatorHats side mark
    # anywhere outside the narrow front-edge projection field.
    if (not np.array_equal(final[:top],original[:top])
            or not np.array_equal(final[bottom:],original[bottom:])
            or not np.array_equal(final[:,:left],original[:,:left])
            or not np.array_equal(final[:,right:],original[:,right:])):
        raise Slot1EmbroideryQAError("Angled transfer changed protected factory stock.")
    ys,xs=np.where(mask> .5)
    target_box=(left+int(xs.min()), top+int(ys.min()),
                left+int(xs.max())+1,top+int(ys.max())+1)
    output = BytesIO()
    Image.fromarray(final,"RGB").save(output,"PNG",optimize=True)
    buffer=BytesIO()
    layer.save(buffer,"PNG",optimize=True)
    return ThreadTransferResult(
        output_png=output.getvalue(),
        thread_layer_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
        extracted_pixels=int(np.count_nonzero(mask>.5)),
        target_box=target_box,
    )
