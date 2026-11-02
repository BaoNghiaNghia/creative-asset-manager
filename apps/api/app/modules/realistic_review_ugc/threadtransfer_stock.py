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


def composite_reference_on_stock_view(
    stock_bytes: bytes,
    reference_png: bytes,
    *,
    stage: str,
    design_size: int = 100,
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
