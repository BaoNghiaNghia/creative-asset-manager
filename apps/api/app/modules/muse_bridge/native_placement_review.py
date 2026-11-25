"""Review-only recovery for exact Native Masters with unknown placement.

An unknown placement is never a render approval. The worker must compare the
actual product image and SHA-matched PNG, explicitly identify a centered front
print, and submit the existing visual QA checks. Known non-front placements
and contradictory metadata cannot be promoted by this lane.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

POLICY = "CAM_NATIVE_PLACEMENT_REVIEW_V1"
ASSET_ID = re.compile(r"^[a-f0-9]{24}$")


@dataclass(frozen=True)
class NativeMatch:
    job_id: str
    asset_id: str
    position: str

    @property
    def locator(self):
        return self.job_id, self.asset_id


def choose_matches(rows, digest):
    """Return the first exact front match, or an unambiguous unknown candidate.

    Rows are already scoped to the tenant/listing and ordered newest first.
    Unknown may enter review only when no completed same-SHA record gives a
    known non-front placement. Do not fall back to another SHA or asset kind.
    """
    front = None
    unknown = None
    conflicting = False
    for row in rows:
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        assets = payload.get("design_assets")
        if not isinstance(assets, list):
            continue
        for a in assets:
            if (not isinstance(a, dict) or a.get("output_sha256") != digest
                    or a.get("artwork_kind") != "native_master_v2"
                    or a.get("method") != "imagegen"):
                continue
            aid = a.get("id")
            if not isinstance(aid, str) or not ASSET_ID.fullmatch(aid):
                continue
            pos = a.get("position")
            if pos == "front" and front is None:
                front = NativeMatch(row.id, aid, pos)
            elif pos == "unknown" and unknown is None:
                unknown = NativeMatch(row.id, aid, pos)
            elif pos not in ("front", "unknown", None):
                conflicting = True
    return front, None if conflicting else unknown


def review_id(match, digest, source_url):
    if not isinstance(source_url, str) or not source_url:
        return None
    return hashlib.sha256(json.dumps(
        [POLICY, match.job_id, match.asset_id, digest, source_url],
        separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def review_contract(match, digest, source_url):
    if match is None or match.position != "unknown":
        return None
    rid = review_id(match, digest, source_url)
    if rid is None:
        return None
    return {
        "policy": POLICY, "originalPosition": "unknown",
        "reviewOnly": True, "placementReviewId": rid,
        "requiredObservedPlacementForPass": "front_center",
        "instructions": (
            "Read the original product image and exact SHA-matched Native Master. "
            "Only pass if the image visibly shows this exact artwork centered on "
            "the FRONT of the garment, with all existing comparative QA checks "
            "true and confidence >= 0.95. Set observedPlacement=front_center "
            "and echo placementReviewId. If back, sleeve, pocket/left chest, "
            "other placement or uncertainty, never mark pass. No rendering "
            "until a genuine accepted visual review. Do not guess from title."
        ),
    }


def approved_unknown(match, digest, source_url, review):
    if match is None or match.position != "unknown" or not isinstance(review, dict):
        return False
    record = review.get("placement_review")
    if not isinstance(record, dict):
        return False
    rid = review_id(match, digest, source_url)
    checks = review.get("checks") or {}
    confidence = review.get("confidence")
    return bool(
        rid and review.get("verdict") == "pass"
        and review.get("artwork_sha256") == digest
        and record.get("policy") == POLICY
        and record.get("observedPlacement") == "front_center"
        and record.get("placementReviewId") == rid
        and record.get("jobId") == match.job_id
        and record.get("assetId") == match.asset_id
        and isinstance(confidence, (int, float)) and confidence >= 0.95
        and isinstance(checks, dict)
        and all(checks.get(k) is True for k in (
            "sourceImageReviewed", "designMatchesSource", "originalTextPreserved",
            "transparentBackgroundChecked", "placementChecked"))
    )
