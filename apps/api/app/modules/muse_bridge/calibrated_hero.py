"""Deterministic flat-ink C1717 hero placement on a *plain* approved shirt.

The printing geometry is computed from a worker-supplied measured fabric-plane
reference. AI-generated finished-print pixels are never used as the position
control. A claimed physical measurement is NOT independent QA.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import math

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from .hero_storage import validate_hero_image
from .story_action_policy import PLACEMENT_TARGET_MM, PLACEMENT_TOLERANCE_MM

COMPOSITOR_VERSION = "CAM_C1717_PLAIN_HERO_NATIVE_MASTER_COMPOSITE_V1"
MAX_MASTER_BYTES = 20 * 1024 * 1024
MAX_PLAIN_IMAGE_BYTES = 12 * 1024 * 1024


def placement_plan(*, width_px: int, height_px: int, lower_collar_y_px: float,
                   garment_reference_width_px: float, garment_reference_width_mm: float,
                   print_center_x_px: float, print_width_mm: float,
                   shirt_hem_y_px: float, artwork_ratio: float) -> dict:
    numbers = (
        width_px, height_px, lower_collar_y_px, garment_reference_width_px,
        garment_reference_width_mm, print_center_x_px, print_width_mm,
        shirt_hem_y_px, artwork_ratio,
    )
    if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in numbers):
        raise HTTPException(422, "Non-finite or missing shirt calibration")
    if not (640 <= width_px <= 4096 and 640 <= height_px <= 4096
            and 60 <= lower_collar_y_px < shirt_hem_y_px < height_px
            and 50 <= garment_reference_width_px <= width_px * 0.95
            and 350 <= garment_reference_width_mm <= 750
            and 120 <= print_width_mm <= 320
            and print_width_mm <= garment_reference_width_mm * 0.65
            and 0.15 <= artwork_ratio <= 4.5):
        raise HTTPException(422, "Invalid C1717 physical calibration or print size")
    pixels_per_mm = garment_reference_width_px / garment_reference_width_mm
    top = round(lower_collar_y_px + PLACEMENT_TARGET_MM * pixels_per_mm)
    width = round(print_width_mm * pixels_per_mm)
    height = round(width / artwork_ratio)
    left = round(print_center_x_px - width / 2)
    right = left + width
    bottom = top + height
    shirt_left = print_center_x_px - garment_reference_width_px / 2
    shirt_right = print_center_x_px + garment_reference_width_px / 2
    if (top <= lower_collar_y_px or top <= 0 or width < 40 or height < 20
            or left < max(0, shirt_left + 8)
            or right > min(width_px, shirt_right - 8)
            or bottom > shirt_hem_y_px - 10 or bottom > height_px
            or top > height_px - 50):
        raise HTTPException(422, "Native Master will not fit below 150mm print anchor on this shirt")
    measured_mm = (top - lower_collar_y_px) / pixels_per_mm
    if abs(measured_mm - PLACEMENT_TARGET_MM) > PLACEMENT_TOLERANCE_MM:
        raise HTTPException(422, "Insufficient image resolution for 150mm calibration")
    return {
        "targetTopInkPx": top,
        "firstInkTopYpx": float(top),
        "lowerCollarBottomYpx": lower_collar_y_px,
        "garmentReferenceWidthPx": garment_reference_width_px,
        "garmentReferenceWidthMm": garment_reference_width_mm,
        "printCenterXpx": print_center_x_px,
        "printWidthMm": print_width_mm,
        "inkBoundingBoxPx": [left, top, right, bottom],
        "pixelsPerMm": round(pixels_per_mm, 7),
        "calculatedGapMm": round(measured_mm, 3),
        "targetGapMm": PLACEMENT_TARGET_MM,
        "toleranceMm": PLACEMENT_TOLERANCE_MM,
        "geometrySource": "worker_supplied_measured_shirt_fabric_plane",
        "independentlyVerified": False,
    }


def composite_native_master_on_plain_hero(*, plain_bytes: bytes, content_type: str,
                                          aspect: str, artwork_png: bytes,
                                          calibration: dict) -> tuple[bytes, dict]:
    """Print transparent source *pixels*, not a model's re-created lettering.

    Caller must independently verify that source shirt is BLANK and the
    physical scale is measured on the same fabric plane; otherwise HOLD.
    No local output file is created.
    """
    if (not 2048 <= len(plain_bytes) <= MAX_PLAIN_IMAGE_BYTES
            or not 256 <= len(artwork_png) <= MAX_MASTER_BYTES
            or not calibration.get("plainC1717ShirtVerified")
            or not calibration.get("noExistingPrintOnPlainSourceVerified")
            or not calibration.get("frontFabricPlaneMeasured")
            or calibration.get("physicalReferenceType") not in {
                "measured_marker_on_worn_garment",
                "measured_c1717_size_template_with_same_plane_verification",
            }
            or not isinstance(calibration.get("calibrationUncertaintyMm"), (int, float))
            or not 0 <= calibration["calibrationUncertaintyMm"] <= 2.5
            or calibration.get("calibrationMethod") not in {
                "measured_c1717_shirt_size_template",
                "measured_reference_on_same_fabric_plane",
            }):
        raise HTTPException(422, "Plain C1717 base and measured fabric-plane calibration required")
    base_meta = validate_hero_image(plain_bytes, aspect, content_type)
    try:
        with Image.open(BytesIO(artwork_png)) as src:
            if src.format != "PNG" or getattr(src, "n_frames", 1) != 1:
                raise ValueError("Native Master must be one transparent PNG")
            src.load()
            if src.width > 8192 or src.height > 8192 or src.width * src.height > 24_000_000:
                raise ValueError("Oversized Native Master")
            art = src.convert("RGBA")
        alpha_bbox = art.getchannel("A").getbbox()
        if alpha_bbox is None:
            raise ValueError("Native Master has no visible ink")
        alpha_min, alpha_max = art.getchannel("A").getextrema()
        if alpha_max == 0 or alpha_min == 255:
            raise ValueError("Native Master must retain transparent pixels")
        # The ink bounding box excludes only *fully transparent* margins.
        # No font replacement, color substitution, or AI redraw.
        art = art.crop(alpha_bbox)
        with Image.open(BytesIO(plain_bytes)) as src:
            base = ImageOps.exif_transpose(src).convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Plain hero or approved transparent Native Master is invalid") from exc
    wanted = placement_plan(
        width_px=base.width, height_px=base.height,
        lower_collar_y_px=calibration["lowerCollarBottomYpx"],
        garment_reference_width_px=calibration["garmentReferenceWidthPx"],
        garment_reference_width_mm=calibration["garmentReferenceWidthMm"],
        print_center_x_px=calibration["printCenterXpx"],
        print_width_mm=calibration["printWidthMm"],
        shirt_hem_y_px=calibration["shirtHemYpx"],
        artwork_ratio=art.width / art.height,
    )
    left, top, right, bottom = wanted["inkBoundingBoxPx"]
    # Subpixel rounding plus physical-reference uncertainty must fit the
    # 145-155mm total contract. A reported +/-10mm source scale can NEVER
    # be disguised as a +/-5mm placement pass.
    total_error_mm = (
        calibration["calibrationUncertaintyMm"] +
        0.5 / wanted["pixelsPerMm"]
    )
    if total_error_mm > PLACEMENT_TOLERANCE_MM:
        raise HTTPException(422, "Measured fabric-plane uncertainty exceeds the 150mm +/-5mm tolerance")
    # Stays on the source photo's actual garment plane. For heavily folded
    # or angled garments, a projection/UV tracking pipeline is necessary:
    # worker must HOLD rather than claim this simple planar fit is valid.
    ink = art.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
    base.alpha_composite(ink, dest=(left, top))
    buf = BytesIO()
    base.convert("RGB").save(buf, format="JPEG", quality=95, subsampling=0)
    result = buf.getvalue()
    rendered_meta = validate_hero_image(result, aspect, "image/jpeg")
    return result, {
        **wanted,
        "policyVersion": COMPOSITOR_VERSION,
        "calibrationMethod": calibration["calibrationMethod"],
        "physicalReferenceType": calibration["physicalReferenceType"],
        "calibrationUncertaintyMm": calibration["calibrationUncertaintyMm"],
        "maximumEstimatedPlacementErrorMm": round(total_error_mm, 3),
        "sourcePlainHeroSha256": base_meta["sha256"],
        "nativeMasterSha256": hashlib.sha256(artwork_png).hexdigest(),
        "sourceIsBlankWorkerReported": True,
        "sourceIsC1717WorkerReported": True,
        "frontalPlaneWorkerReported": True,
        "notAnIndependentPrintOrMotionQaPass": True,
        "renderedHeroSha256": rendered_meta["sha256"],
        "renderedHeroWidthPx": rendered_meta["width"],
        "renderedHeroHeightPx": rendered_meta["height"],
    }
