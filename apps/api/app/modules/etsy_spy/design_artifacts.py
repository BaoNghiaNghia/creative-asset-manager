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
    """SHA-bound, bounded read projection; no image processing on Picked reads."""
    from .color_match_palette import ALGORITHM, GARMENT_COLORS, LEGACY_COLOR_IDS
    match = raw.get("color_match")
    if not isinstance(match, dict):
        return None
    version = match.get("version")
    sms_versions = (ALGORITHM, "colormatch-sms-c1717-v6",
                    "colormatch-sms-c1717-v5", "colormatch-sms-c1717-v4")
    allowed = ({color_id for color_id, _ in GARMENT_COLORS}
               if version in sms_versions else LEGACY_COLOR_IDS)
    colors = match.get("recommended")
    max_recommendations = (10 if version in (ALGORITHM, "colormatch-sms-c1717-v6",
                                             "colormatch-sms-c1717-v5",
                                             "colormatch-pixel-v2") else
                           16 if version in ("colormatch-sms-c1717-v4",
                                             "colormatch-pixel-v3") else 4)
    zero_allowed = (version in (ALGORITHM, "colormatch-sms-c1717-v6")
                    and match.get("insufficient_safe_colors") is True)
    if (version not in (*sms_versions, "colormatch-pixel-v3",
                        "colormatch-pixel-v2", "colormatch-pixel-v1") or
            match.get("output_sha256") != digest or
            not isinstance(colors, list) or
            not (0 if zero_allowed else 1) <= len(colors) <= max_recommendations or
            any(not isinstance(c, str) or c not in allowed for c in colors) or
            len(set(colors)) != len(colors)):
        return None
    if version == ALGORITHM:
        unsafe = match.get("unsafe_colors")
        eligible = match.get("eligible_colors")
        if (not isinstance(unsafe, list) or len(unsafe) > len(allowed) or
                not isinstance(eligible, list) or len(eligible) > len(allowed) or
                any(not isinstance(c, str) or c not in allowed for c in unsafe + eligible) or
                len(set(unsafe)) != len(unsafe) or len(set(eligible)) != len(eligible) or
                not set(unsafe).isdisjoint(eligible) or
                not set(unsafe).isdisjoint(colors) or
                not set(colors).issubset(eligible) or
                (len(colors) < 7) != (match.get("insufficient_safe_colors") is True) or
                match.get("readability_check") != "region_ink_alpha_composite_v2" or
                match.get("method") != "design_region_alpha_sales_prior"):
            return None
        analysis = match.get("analysis")
        if not isinstance(analysis, dict):
            return None
        resolution = analysis.get("resolution")
        regions = analysis.get("regions")
        if (not isinstance(resolution, list) or len(resolution) != 2 or
                any(type(n) is not int or not 1 <= n <= 768 for n in resolution) or
                analysis.get("source") not in ("medium", "thumbnail") or
                not isinstance(regions, list) or len(regions) > 12 or
                type(analysis.get("faded_ink")) is not bool or
                type(analysis.get("print_review_required")) is not bool or
                type(analysis.get("text_regions")) is not int or
                not 0 <= analysis["text_regions"] <= 16 or
                type(analysis.get("microtext_regions")) is not int or
                not 0 <= analysis["microtext_regions"] <= 16):
            return None
        clean_regions = []
        for region in regions:
            if not isinstance(region, dict):
                return None
            bbox = region.get("bbox")
            opacity = region.get("opacity")
            if (region.get("kind") not in ("probable_text", "critical_ink") or
                    not isinstance(region.get("pigment"), str) or
                    len(region["pigment"]) > 40 or
                    not isinstance(bbox, list) or len(bbox) != 4 or
                    any(type(n) is not int or not 0 <= n <= 1000 for n in bbox) or
                    bbox[0] > bbox[2] or bbox[1] > bbox[3] or
                    type(opacity) not in (float, int) or not 0 <= opacity <= 1):
                return None
            clean_regions.append({
                "kind": region["kind"], "pigment": region["pigment"],
                "bbox": bbox, "opacity": round(float(opacity), 2),
            })
        return {
            "version": version, "output_sha256": digest,
            "recommended": colors, "unsafe_colors": unsafe,
            "eligible_colors": eligible,
            "insufficient_safe_colors": len(colors) < 7,
            "readability_check": "region_ink_alpha_composite_v2",
            "method": "design_region_alpha_sales_prior",
            "analysis": {
                "resolution": resolution, "source": analysis["source"],
                "regions": clean_regions, "text_regions": analysis["text_regions"],
                "microtext_regions": analysis["microtext_regions"],
                "faded_ink": analysis["faded_ink"],
                "print_review_required": analysis["print_review_required"],
            },
        }
    if version == "colormatch-sms-c1717-v6":
        unsafe = match.get("unsafe_colors")
        if (len(colors) < 7 and match.get("insufficient_safe_colors") is not True) or (
                not isinstance(unsafe, list) or len(unsafe) > len(allowed) or
                any(not isinstance(c, str) or c not in allowed for c in unsafe) or
                len(set(unsafe)) != len(unsafe) or
                not set(unsafe).isdisjoint(colors) or
                match.get("readability_check") != "foreground_ink_connected_components_proxy_v1"):
            return None
        return {
            "version": version, "output_sha256": digest,
            "recommended": colors, "unsafe_colors": unsafe,
            "insufficient_safe_colors": len(colors) < 7,
            "readability_check": "foreground_ink_connected_components_proxy_v1",
            "method": "thumbnail_ink_readability_sales_prior",
        }
    return {
        "version": version, "output_sha256": digest,
        "recommended": colors, "method": (
            "thumbnail_pixel_contrast_sales_prior"
            if match.get("method") == "thumbnail_pixel_contrast_sales_prior"
            and version == "colormatch-sms-c1717-v5" else "thumbnail_pixel_contrast"
        ),
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