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
        out.append(entry(
            "fast", "unknown", 1, "fast_result", result["output_sha256"],
            "fast_cpu", "needs_review", result.get("finished_at"),
        ))
    if candidate.get("status") == "review_candidate" and isinstance(candidate.get("output_sha256"), str):
        out.append(entry(
            "candidate", "unknown", 1, "legacy_candidate",
            candidate["output_sha256"], candidate.get("method") or "local_layered",
            "review_candidate", candidate.get("created_at"),
        ))

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
        # Tiny precomputed advisory metadata; no image reads, extra SQL, or
        # ColorMatch calculation on the hot Picked listing / modal path.
        color_match = raw.get("color_match")
        if isinstance(color_match, dict):
            from .color_match_palette import ALGORITHM, GARMENT_COLORS
            allowed = {color_id for color_id, _ in GARMENT_COLORS}
            choices = color_match.get("recommended")
            if (color_match.get("version") == ALGORITHM and
                    color_match.get("output_sha256") == digest and
                    isinstance(choices, list) and 1 <= len(choices) <= 4 and
                    len(set(str(c) for c in choices)) == len(choices) and
                    all(isinstance(c, str) and c in allowed for c in choices)):
                mapped["color_match"] = {
                    "version": ALGORITHM,
                    "output_sha256": digest,
                    "recommended": choices,
                    "method": "thumbnail_pixel_contrast",
                }
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
