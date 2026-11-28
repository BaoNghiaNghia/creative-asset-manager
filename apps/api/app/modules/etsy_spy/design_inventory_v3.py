"""Bounded optional metadata from the source-verified Image Extension V3."""
from __future__ import annotations

import hashlib
import json
import math
import re

KINDS = {"single_design", "repeated_view", "two_sided", "couple_set", "family_set",
         "multiple_variants", "multiple_placements", "mixed", "uncertain"}
SIDES = {"front", "back", "side", "unknown"}
REGIONS = {"front_center": "front", "back": "back", "left_chest": "left_chest",
           "right_chest": "right_chest", "left_sleeve": "left_sleeve",
           "right_sleeve": "right_sleeve", "sleeve_unspecified": "other",
           "neck": "neck", "hem": "hem", "other": "other"}
FIELDS = {"inventory_version", "listing_type", "design_evidence_id", "artwork_id",
          "ink_variant_id", "garment_group_id", "set_id", "view_id", "view_side",
          "observations", "crop_bbox_normalized", "bbox_normalized", "crop_sha256", "region"}
OBS_FIELDS = {"observation_id", "view_id", "garment_group_id", "set_id", "view_side",
              "bbox_normalized"}


def _id(value, prefix):
    return isinstance(value, str) and re.fullmatch(prefix + r"[0-9]{2}", value) is not None


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _box(value):
    return (isinstance(value, list) and len(value) == 4 and
            all(type(n) in (float, int) and math.isfinite(n) and 0 <= n <= 1 for n in value) and
            value[0] < value[2] and value[1] < value[3])


def _contains(outer, inner):
    return (outer[0] <= inner[0]+1e-8 and outer[1] <= inner[1]+1e-8 and
            outer[2] >= inner[2]-1e-8 and outer[3] >= inner[3]-1e-8)


def validate_design_inventory_v3(value, source_sha, position):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("invalid inventory V3 fields")
    if (type(value["inventory_version"]) is not int or value["inventory_version"] != 3 or
            not isinstance(value["listing_type"], str) or value["listing_type"] not in KINDS or
            not _sha(source_sha) or not _sha(value["crop_sha256"]) or
            not isinstance(value["region"], str) or REGIONS.get(value["region"]) != position or
            not _id(value["artwork_id"], "D") or not _id(value["ink_variant_id"], "I") or
            not _id(value["garment_group_id"], "G") or not _id(value["view_id"], "V") or
            not (value["set_id"] is None or _id(value["set_id"], "S")) or
            not isinstance(value["view_side"], str) or value["view_side"] not in SIDES or
            not _box(value["bbox_normalized"]) or not _box(value["crop_bbox_normalized"]) or
            not _contains(value["crop_bbox_normalized"], value["bbox_normalized"])):
        raise ValueError("invalid inventory V3 identity or geometry")
    side = {"front_center": "front", "left_chest": "front", "right_chest": "front",
            "back": "back"}.get(value["region"])
    if side and side != value["view_side"]:
        raise ValueError("inventory V3 side mismatch")
    evidence = hashlib.sha256(json.dumps([
        "cam-design-evidence-v3", source_sha, value["region"],
        [math.floor(n*10000+.5) for n in value["bbox_normalized"]]
    ], separators=(",", ":")).encode("ascii")).hexdigest()
    if evidence != value["design_evidence_id"]:
        raise ValueError("inventory V3 evidence hash mismatch")
    observations = value["observations"]
    if not isinstance(observations, list) or not 1 <= len(observations) <= 8:
        raise ValueError("invalid inventory V3 observation count")
    ids, selected = set(), False
    for o in observations:
        if (not isinstance(o, dict) or set(o) != OBS_FIELDS or
                not _id(o["observation_id"], "O") or o["observation_id"] in ids or
                not _id(o["view_id"], "V") or not _id(o["garment_group_id"], "G") or
                not (o["set_id"] is None or _id(o["set_id"], "S")) or
                not isinstance(o["view_side"], str) or o["view_side"] not in SIDES or
                (side and o["view_side"] != side) or not _box(o["bbox_normalized"])):
            raise ValueError("invalid inventory V3 observation")
        ids.add(o["observation_id"])
        if all(o[k] == value[k] for k in ("view_id","garment_group_id","set_id","view_side","bbox_normalized")):
            selected = True
    if not selected:
        raise ValueError("inventory V3 selected observation missing")
    return value
