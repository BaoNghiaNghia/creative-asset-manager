"""CAM V3 single physical print-box authority, compatible with Extension JS.

Never recompute a pack width from SMS mockup advice. The lock is a target,
not measured physical QA. Only front-centered C1717 is admitted for P2D.
"""
from __future__ import annotations

import hashlib
import json
import math

from . import policy as p

AUTHORITY = "CAM_PRINT_BOX_SINGLE_AUTHORITY_V1"
SCHEMA = "cam-flat-ink-placement-v3"
# Exact field order of createPrintPlacementV3 in the installed Bridge.
ORDER = (
    "schema", "print_type", "decoration_method", "placement_mode",
    "placement_side", "garment_code", "artwork_sha256", "top_offset_mm",
    "top_offset_tolerance_mm", "hem_clearance_min_mm", "print_center_x",
    "horizontal_alignment", "print_width_mm", "width_source", "ink_aspect_ratio",
    "requires_lower_collar_and_top_ink_landmarks", "placement_locked",
    "shared_placement_across_roles", "shared_placement_across_video", "print_scale_locked",
)
FIXED = {
    "schema": SCHEMA, "print_type": "print", "decoration_method": "flat_ink_print_only",
    "placement_mode": "fixed_top_offset", "garment_code": "C1717",
    "top_offset_mm": 150, "top_offset_tolerance_mm": 5, "hem_clearance_min_mm": 50,
    "print_center_x": 0.5, "horizontal_alignment": "garment_panel_center",
    "requires_lower_collar_and_top_ink_landmarks": True, "placement_locked": True,
    "shared_placement_across_roles": True, "shared_placement_across_video": True,
    "print_scale_locked": True,
}
# Existing SMS Printing 2D Front snapshot verified 2026-11-20.
# Width band is a production limit, never the assigned artwork width.
SMS_MAX = {"S":254,"M":279.4,"L":304.8,"XL":330.2,"2XL":355.6,"3XL":381,"4XL":381}
SIDES = {"front", "front_center", "center_chest", "middle_chest"}


def _number(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 < value <= 500 and round(value, 2) == value)


def lock_digest(body):
    # For this bounded ASCII/numeric contract JSON.stringify emits integral
    # numbers without .0. Rebuild insertion order explicitly; DB JSON order
    # is not an authority. Do not use the sorted plan digest for this lock.
    ordered = {}
    for key in ORDER:
        value = body[key]
        if type(value) is float and value.is_integer():
            value = int(value)
        ordered[key] = value
    raw = json.dumps(ordered, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def validate(lock, *, artwork_sha, placement_sha, mockup_size):
    p.require(isinstance(lock, dict) and set(lock)==set(ORDER)|{"lock_sha256"},
              "placement_lock_unverified")
    p.require(all(type(lock[k]) is type(v) and lock[k]==v for k,v in FIXED.items())
              and isinstance(lock["placement_side"], str) and lock["placement_side"] in SIDES
              and p.valid_sha(artwork_sha) and lock["artwork_sha256"]==artwork_sha
              and isinstance(lock["width_source"], str)
              and lock["width_source"] in ("sms_preview_advisory","explicit_design_width")
              and _number(lock["print_width_mm"]) and _number(lock["ink_aspect_ratio"])
              and p.valid_sha(placement_sha) and lock["lock_sha256"]==placement_sha,
              "placement_lock_unverified")
    p.require(lock_digest(lock)==placement_sha,"placement_lock_unverified")
    p.require(isinstance(mockup_size,str) and mockup_size in SMS_MAX,
              "sms_production_limit_unverified")
    maximum=SMS_MAX[mockup_size]
    p.require(lock["print_width_mm"]<=maximum,"print_box_exceeds_sms_factory_limit")
    return {
        "printBoxAuthority": AUTHORITY,
        "placementLock": dict(lock),
        "smsProductionMaxWidthMm": maximum,
        "smsProductionSnapshotDate": "2026-11-20",
        "mockupSize": mockup_size,
        "physicalCalibrationVerified": False,
    }
