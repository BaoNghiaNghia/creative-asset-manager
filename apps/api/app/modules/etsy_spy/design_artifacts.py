"""Versioned one-to-many Design mapping for a Picked Etsy listing.

ProcessingJobModel remains the authoritative tenant/listing/pick-generation
record. Its JSON contains bounded, immutable design-output descriptors; PNGs
live in the existing private, tenant-hashed extraction storage. No migration,
Scout DB mutation or generated image URL from an untrusted host is required.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

DESIGN_POSITIONS = frozenset((
    "front", "back", "left_chest", "right_chest",
    "left_sleeve", "right_sleeve", "neck", "hem", "other",
    "unknown",
))
DESIGN_METHODS = frozenset(("fast_cpu", "local_layered", "imagegen", "manual"))
MAX_DESIGNS_PER_JOB = 12
DESIGN_BASE = "/api/v1/etsy-spy/image-agents/designs"


def design_asset_id(job_id: str, position: str, variant: int, digest: str) -> str:
    return sha256(f"{job_id}:{position}:{variant}:{digest}".encode("ascii")).hexdigest()[:24]


def design_media_path(job_id: str, asset_id: str, *, thumb: bool = False) -> str:
    name = "thumbnail" if thumb else "original"
    return f"{DESIGN_BASE}/{job_id}/{asset_id}/{name}"


def _validated_color_match(raw: dict, digest: str) -> dict | None:
    """Project bounded advisory color metadata for mapped and legacy images.

    Includes candidate/fast sources when they have the same checked SHA,
    without decoding PNGs or introducing any database/network work.
    """
    from .color_match_palette import ALGORITHM, GARMENT_COLORS
    match = raw.get("color_match")
    if not isinstance(match, dict):
        return None
    allowed = {color_id for color_id, _ in GARMENT_COLORS}
    colors = match.get("recommended")
    if (match.get("version") != ALGORITHM or
            match.get("output_sha256") != digest or
            not isinstance(colors, list) or not 1 <= len(colors) <= 4 or
            any(not isinstance(c, str) or c not in allowed for c in colors) or
            len(set(colors)) != len(colors)):
        return None
    return {
        "version": ALGORITHM, "output_sha256": digest,
        "recommended": colors, "method": "thumbnail_pixel_contrast",
    }


def project_design_assets(job: Any) -> list[dict]:
    """Return only evidence-backed private image URLs, never inferred positions."""
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    candidate = (payload.get("review_candidate")
                 if isinstance(payload.get("review_candidate"), dict) else {})
    out: list[dict] = []

    def entry(asset_id: str, position: str, variant: int, source: str,
              digest: str, method: str, status: str, created_at: str | None) -> dict:
        return {
            "id": asset_id, "job_id": job.id, "listing_key": job.entity_id,
            "position": position, "variant": variant, "method": method,
            "status": status, "source": source, "output_sha256": digest,
            "thumbnail_url": design_media_path(job.id, asset_id, thumb=True),
            "original_url": design_media_path(job.id, asset_id),
            "print_ready": False, "rights_status": "unverified",
            "manual_qa_required": True, "created_at": created_at,
        }

    if result.get("status") == "needs_review" and isinstance(result.get("output_sha256"), str):
        fast = entry(
            "fast", "unknown", 1, "fast_result", result["output_sha256"],
            "fast_cpu", "needs_review", result.get("finished_at"),
        )
        colors = _validated_color_match(result, result["output_sha256"])
        if colors:
            fast["color_match"] = colors
        out.append(fast)
    if candidate.get("status") == "review_candidate" and isinstance(candidate.get("output_sha256"), str):
        legacy = entry(
            "candidate", "unknown", 1, "legacy_candidate",
            candidate["output_sha256"], candidate.get("method") or "local_layered",
            "review_candidate", candidate.get("created_at"),
        )
        colors = _validated_color_match(candidate, candidate["output_sha256"])
        if colors:
            legacy["color_match"] = colors
        out.append(legacy)

    for raw in payload.get("design_assets", []) if isinstance(payload.get("design_assets"), list) else []:
        if not isinstance(raw, dict):
            continue
        asset_id, digest = raw.get("id"), raw.get("output_sha256")
        if (not isinstance(asset_id, str) or len(asset_id) != 24 or
            not all(c in "0123456789abcdef" for c in asset_id) or
            not isinstance(digest, str) or len(digest) != 64 or
            raw.get("position") not in DESIGN_POSITIONS or
            raw.get("method") not in DESIGN_METHODS or
            not isinstance(raw.get("variant"), int)):
            continue
        mapped = entry(
            asset_id, raw["position"], raw["variant"], "mapped",
            digest, raw["method"], "needs_review", raw.get("created_at"),
        )
        if raw.get("artwork_kind") == "native_master_v2":
            mapped["artwork_kind"] = "native_master_v2"
            for key in ("native_width", "native_height"):
                size = raw.get(key)
                if type(size) is int and 256 <= size <= 4096:
                    mapped[key] = size
        # No image reads, extra SQL, or analysis on the hot Picked listing path.
        colors = _validated_color_match(raw, digest)
        if colors:
            mapped["color_match"] = colors
        out.append(mapped)
    return out[:MAX_DESIGNS_PER_JOB + 2]


def latest_designs_for_listing(jobs: list[Any]) -> list[dict]:
    """Newest first; no duplicate asset IDs; bounded projection per listing."""
    result: list[dict] = []
    seen = set()
    for job in jobs:
        if job.cancellation_requested:
            continue
        for item in project_design_assets(job):
            key = (item["job_id"], item["id"])
            if key not in seen:
                result.append(item)
                seen.add(key)
                if len(result) == MAX_DESIGNS_PER_JOB + 2:
                    return result
    return result
