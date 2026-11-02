"""Single calibration for Products Stage 2 front, 3/4 and true side.

The front cap is the sole size authority. Subsequent angles project the same
embroidered width/height, not an arbitrary size inferred from the macro photo.
V1 is opt-in per queued job. Already queued jobs retain their original mode.
All coordinates are relative to an *unaltered factory stock photograph*;
no artificial angle or cap body is generated.
"""
from __future__ import annotations

import math
from typing import Any

_VERSION = "front-locked-v1"  # immutable saved angle master, including historic jobs
_FRONT_GUIDE_VERSION = "front-guide-v3"
_PREVIOUS_FRONT_GUIDE_VERSION = "front-guide-v2"
_FRONT_SAFE_BOX = (.145, .245, .855, .620)
# Normalized physical front-facing fields. Side90 has only a slim visible edge.
_ANGLE_ZONE = {
    "prod_s2_ec2": (.205, .245, .760, .670),
    "prod_s2_ec3": (.220, .280, .460, .545),
}


class EmbroideryScaleError(ValueError):
    pass


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise EmbroideryScaleError("Invalid embroidery scale coordinate.")
    number = float(value)
    if not math.isfinite(number):
        raise EmbroideryScaleError("Nonfinite embroidery scale coordinate.")
    return number


def _bbox(box: object) -> tuple[float, float, float, float]:
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise EmbroideryScaleError("Embroidery master box must contain four coordinates.")
    x0, y0, x1, y1 = [_finite(v) for v in box]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise EmbroideryScaleError("Embroidery master box is outside the photograph.")
    if x1-x0 < .025 or y1-y0 < .018:
        raise EmbroideryScaleError("Master embroidery is too small for reliable angle projection.")
    return x0, y0, x1, y1


def front_scale_contract(front_placement: object) -> dict[str, Any]:
    """Lock a fresh Slot 1 to the user's SAVED front embroidery rectangle.

    The old V1 guessed a fixed 38% width regardless of the visible blueprint.
    The V3 width/height constraints match the Blueprint's 95%/92% saved *field*
    at 100% zoom, contain-fitting the actual reference yarn without stretching.
    """
    from app.modules.realistic_review_ugc.front_placement import job_front_placement
    v = job_front_placement(front_placement)
    x0, y0, x1, y1 = _FRONT_SAFE_BOX
    guide = (
        max(x0, v["left"]), max(y0, v["top"]),
        min(x1, v["left"] + v["width"]),
        min(y1, v["top"] + v["height"]),
    )
    if guide[2] - guide[0] < .12 or guide[3] - guide[1] < .06:
        raise EmbroideryScaleError("Saved front embroidery guide is outside the factory crown.")
    zoom = max(.4, min(2., v.get("design_size", 100) / 100.))
    width, height = guide[2] - guide[0], guide[3] - guide[1]
    # Contain-fit with safe perimeter; zoom is bounded to the actual field.
    target_w = min(width * .98, width * .95 * zoom)
    target_h = min(height * .98, height * .92 * zoom)
    return {
        "version": _FRONT_GUIDE_VERSION,
        "center_x": round((guide[0] + guide[2]) / 2, 6),
        "center_y": round((guide[1] + guide[3]) / 2, 6),
        "target_width": round(target_w, 6),
        "max_height": round(target_h, 6),
        "safe_box": [round(t, 6) for t in guide],
        "design_size": int(v.get("design_size", 100)),
        "fit_mode": "contain",
        "guide_source": "saved_front_embroidery_area",
    }


def front_transfer_box(contract: dict, source_ratio: float,
                       photo_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Contain-fit reference yarn at a fixed front crown center and physical size."""
    if contract.get("version") not in (_VERSION, _PREVIOUS_FRONT_GUIDE_VERSION, _FRONT_GUIDE_VERSION) or source_ratio <= 0 or not math.isfinite(source_ratio):
        raise EmbroideryScaleError("Unknown front embroidery scale contract.")
    width, height = photo_size
    if min(width, height) < 64:
        raise EmbroideryScaleError("Stock image is too small for front transfer.")
    x0, y0, x1, y1 = (
        _bbox(contract["safe_box"]) if contract.get("version") in (_FRONT_GUIDE_VERSION, _PREVIOUS_FRONT_GUIDE_VERSION)
        else _FRONT_SAFE_BOX
    )
    cx, cy = _finite(contract["center_x"]), _finite(contract["center_y"])
    safety = .98 if contract.get("version") in (_FRONT_GUIDE_VERSION, _PREVIOUS_FRONT_GUIDE_VERSION) else .94
    desired_w = min(_finite(contract["target_width"])*width, (x1-x0)*width*safety)
    desired_h = min(_finite(contract["max_height"])*height, (y1-y0)*height*safety)
    tw = min(desired_w, desired_h * source_ratio)
    th = tw / source_ratio
    if tw < 12 or th < 12:
        raise EmbroideryScaleError("The fitted embroidery is too small.")
    tw, th = round(tw), round(th)
    x = round(cx * width - tw/2)
    y = round(cy * height - th/2)
    if not (x0*width <= x and x+tw <= x1*width
            and y0*height <= y and y+th <= y1*height):
        raise EmbroideryScaleError("Embroidery exceeds the calibrated front crown.")
    return x, y, x + tw, y + th


def saved_master_box(pixel_box: object, width: int, height: int,
                     source_revision: str | None = None,
                     reference_remote_file_id: str | None = None) -> dict[str, Any]:
    """Persist with immutable Slot 1 output-version metadata (no schema migration)."""
    if width <= 0 or height <= 0 or not isinstance(pixel_box, (list, tuple)) or len(pixel_box) != 4:
        raise EmbroideryScaleError("Missing completed front embroidery bounds.")
    box = _bbox([pixel_box[0]/width, pixel_box[1]/height,
                 pixel_box[2]/width, pixel_box[3]/height])
    return {
        "version": _VERSION,
        "front_box": [round(v, 6) for v in box],
        "source_revision": source_revision,
        "reference_remote_file_id": reference_remote_file_id,
    }


def pinned_master(snapshot: object, *, reference_remote_file_id: str | None = None,
                  source_revision: str | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("version") != _VERSION:
        raise EmbroideryScaleError("Saved Slot 1 embroidery placement is missing.")
    box = _bbox(snapshot.get("front_box"))
    if (reference_remote_file_id is not None
            and snapshot.get("reference_remote_file_id") not in (None, reference_remote_file_id)):
        raise EmbroideryScaleError("Placement belongs to a different Embroidery Reference.")
    if (source_revision is not None and snapshot.get("source_revision") not in (None, source_revision)):
        raise EmbroideryScaleError("Placement belongs to a different source revision.")
    return {**snapshot, "front_box": [round(v, 6) for v in box]}


def projected_angle_box(master: dict, stage: str,
                        photo_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Use the same physical master scale, with expected foreshortening."""
    master = pinned_master(master)
    if stage not in _ANGLE_ZONE:
        raise EmbroideryScaleError("Unknown calibrated angle.")
    width, height = photo_size
    x0, y0, x1, y1 = _ANGLE_ZONE[stage]
    f0, f1, f2, f3 = master["front_box"]
    front_w, front_h = f2-f0, f3-f1
    if stage == "prod_s2_ec2":
        # Factory three-quarter stock: keep ~78% of frontal apparent width.
        # This shrinks oversized 3/4 lettering without reshooting the crown.
        size_w = min(.34, front_w * .78)
        size_h = min(.225, front_h * .86)
        cx, cy = .475, .449
    else:
        # True 90-degree side: a *sliver* of the physically same front
        # embroidery. Do not make a full slogan readable on a side panel.
        size_w = min(.055, max(.027, front_w * .13))
        size_h = min(.22, front_h * .92)
        cx, cy = .318, .438
    size_w = min(size_w, (x1-x0)*.87)
    size_h = min(size_h, (y1-y0)*.87)
    px_w, px_h = max(10, round(size_w*width)), max(10, round(size_h*height))
    px_x, px_y = round(cx*width - px_w/2), round(cy*height - px_h/2)
    if not (px_x >= x0*width and px_x+px_w <= x1*width
            and px_y >= y0*height and px_y+px_h <= y1*height):
        raise EmbroideryScaleError("Projected embroidery would cross a factory side seam.")
    return px_x, px_y, px_x+px_w, px_y+px_h


# Calibrated manufacturer stock presets for the no-ImageGen raster path.
# These rectangles mark the visible FRONT crown (not the larger lateral panel).
# Their derived quads are transformed ONLY within these safe zones.
_RASTER_ANGLE_ZONE = {
    "prod_s2_ec2": (.230, .300, .750, .620),
    "prod_s2_ec3": (.230, .290, .420, .550),
}


def projected_raster_box(master: dict, stage: str,
                         photo_size: tuple[int, int], *,
                         side_front_fallback: bool = False) -> tuple[int, int, int, int]:
    """Fixed template-dependent target preserving physical Slot 1 size.

    The original Slot 1 box is actual sewn yarn, not the macro photo extent.
    A 3/4 view foreshortens the full design. A true right-side 90-degree view
    exposes only the rightmost edge, with the lateral GatorHats logo protected.
    """
    source = pinned_master(master)["front_box"]
    if stage not in _RASTER_ANGLE_ZONE:
        raise EmbroideryScaleError("Unknown factory stock raster placement angle.")
    width, height = photo_size
    if min(width, height) < 512:
        raise EmbroideryScaleError("Manufacturer stock resolution is too small.")
    # When the actual side stock hides the FRONT crown, a new Slot 3 job
    # uses the verified front-right 3/4 photograph as a clearly marked
    # temporary side-front fallback. Never fabricate a camera angle.
    zone = ("prod_s2_ec2" if stage == "prod_s2_ec3" and side_front_fallback
            else stage)
    z0, z1, z2, z3 = _RASTER_ANGLE_ZONE[zone]
    front_w, front_h = source[2]-source[0], source[3]-source[1]
    if stage == "prod_s2_ec2" or side_front_fallback:
        # Single uniform field fit; never distort typography. The second
        # view is slightly more foreshortened but retains all visible letters.
        projected_w = front_w*(.90 if stage == "prod_s2_ec2" else .78)
        projected_h = front_h*(.93 if stage == "prod_s2_ec2" else .89)
        field_fit = min(1., ((z2-z0)*.93)/projected_w,
                        ((z3-z1)*.89)/projected_h)
        target_w, target_h = projected_w*field_fit, projected_h*field_fit
        cx, cy = (.490, .454) if stage == "prod_s2_ec2" else (.50, .454)
    else:
        # Already-pinned side-90 jobs retain historic physical edge geometry.
        target_w = min(front_w*.13, .060, (z2-z0)*.72)
        target_h = min(front_h*.91, (z3-z1)*.82)
        cx, cy = .318, .434
    tw, th = round(target_w*width), round(target_h*height)
    if min(tw, th) < 11:
        raise EmbroideryScaleError("Projected embroidery would not be visible.")
    x, y = round(cx*width - tw/2), round(cy*height - th/2)
    if not (z0*width <= x and x+tw <= z2*width
            and z1*height <= y and y+th <= z3*height):
        raise EmbroideryScaleError("Projected yarn exceeds the verified factory front crown.")
    return x, y, x+tw, y+th


def angle_scale_prompt(master: dict, stage: str) -> str:
    """Provider sees only relative physical geometry, not an extra input PNG."""
    view = projected_angle_box(master, stage, (1000, 1000))
    x0, y0, x1, y1 = view
    return (
        f"PHYSICALLY LOCKED EMBROIDERY SIZE (from saved Slot 1): "
        f"target visible yarn bounding box on the original full stock frame "
        f"center=({(x0+x1)/20:.1f}%,{(y0+y1)/20:.1f}%), "
        f"apparent width={(x1-x0)/10:.1f}%, height={(y1-y0)/10:.1f}%. "
        "This describes thread placement, NOT a mask or rectangle to draw. "
        "Do not invent a new crop, rotate the camera, rescale the hat or enlarge "
        "lettering to fill the crown. Preserve the exact thread reference "
        "glyph shapes and scale relative to the real front cap. "
        + ("Only the physically occluded frontmost portion is allowed at a true "
           "90-degree side; never display an entire slogan on the lateral panel."
           if stage == "prod_s2_ec3" else
           "Keep embroidery centered in the visible front panel, with "
           "space between the letters and all seams.")
    )
