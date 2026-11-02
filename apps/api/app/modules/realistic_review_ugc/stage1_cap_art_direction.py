"""Cap-first Stage 1 composition and non-blocking geometric QA.

A 2:1 front embroidery design field is consistent with CAM's calibrated
Valucap 8869 stock area; actual placement remains Stage 2's responsibility.
"""
from __future__ import annotations

from typing import Any, Mapping

CAP_FIT_PROFILE = "valucap-8869-cap-first-v1"
MAX_ART_RATIO = 2.80
MIN_ART_RATIO = 1.18

CAP_LAYOUT_BLUEPRINTS = (
    "01 TYPOGRAPHY FIRST: deliberate compact two-tier lettering, unequal hierarchy and memorable original letter shapes; a clean text-only result is valid.",
    "02 CREATIVE TWIST: warm heritage typography, one dominant meaningful word and at most one subtle detail tied to the phrase.",
    "03 CREATIVE TWIST: integrate a short fluid stitched script accent into robust display lettering; do not turn the entire quote into tiny script.",
    "04 CREATIVE TWIST: a shallow cap-crown arch over a short upper word group, anchored by large stable lower lettering; not a long bowed banner.",
    "05 CREATIVE TWIST: a restrained word-to-word interaction or letter counter play inside a two-row embroidery block, not a printed ticket or sticker.",
    "06 EXPLORE: a distinctive handcrafted serif/display hybrid, strong but open counters, warmer material character than generic varsity blocks.",
    "07 EXPLORE: expressive hand-lettered rhythm inside two or three compact horizontal rows; never diagonal single-line lettering.",
    "08 EXPLORE: measured staggered letter proportions and original negative space, without increasing the rectangular design footprint.",
    "09 EXPLORE: crafted retro lettering with rhythmic tiny flourish, no perspective distortion or arbitrary recolored duplicate.",
)


def balanced_line_groups(phrase: str) -> tuple[str, ...]:
    """Advisory line grouping only, never rewrite or truncate the source quote."""
    words = phrase.split()
    if len(words) < 2:
        return (phrase,)
    candidates = []
    for index in range(1, len(words)):
        first = " ".join(words[:index])
        second = " ".join(words[index:])
        score = abs(len(first) - len(second)) + (6 if min(len(first), len(second)) < 4 else 0)
        candidates.append((score, index, first, second))
    _, _, first, second = min(candidates)
    return first, second


def style_language(keyword: str) -> str:
    tokens = keyword.casefold().replace("-", " ").split()
    if any(token in tokens for token in ("cowboy", "cowgirl", "western", "rodeo", "ranch", "yeehaw")):
        return (
            "Western heritage craft: clear expressive western serif letter anatomy "
            "with real directional stitch grain. Prefer rust/saddle-brown major lettering, "
            "brass or cream supporting lettering, and restrained dusty teal connecting "
            "flourishes or one small stitched star if meaningful. These are style "
            "cues, not a request to copy any reference artwork. Avoid blue-only "
            "varsity typography, exaggerated plastic puff, heavy bevels or flat ink."
        )
    return (
        "Choose a meaning-specific handcrafted typography character (retro, "
        "humanist, expressive script or editorial as fitting). Use a restrained "
        "harmonious palette and real stitch direction instead of plain digital "
        "font artwork or a fixed royal-blue block-font recipe."
    )


def cap_first_direction(plan: Mapping[str, Any], slot: int) -> dict[str, Any]:
    if not 1 <= slot <= len(CAP_LAYOUT_BLUEPRINTS):
        raise ValueError("Stage 1 cap-first requires one of nine slots")
    spec = plan["slots"][slot - 1]
    concept = spec["concept"]
    phrase = plan["keyword_exact"]
    return {
        "exact_phrase": phrase,
        "suggested_two_line_break": list(balanced_line_groups(phrase)),
        "meaning_and_emotion": plan["context"].get("meaning_emotion", ""),
        "style_language": style_language(phrase),
        "family": spec["mode"],
        "variant": spec["variant"],
        "concept_name": concept["name"],
        "creative_hook": concept["creative_hook"],
        "brief_variant_direction": spec["variant_recipe"],
        "cap_layout_blueprint": CAP_LAYOUT_BLUEPRINTS[slot - 1],
        "cap_fit_profile": CAP_FIT_PROFILE,
    }


def cap_fit_advisory(alpha_bbox: object) -> dict[str, Any]:
    """Image geometry evidence only: no automatic Codex retries or false QA claims."""
    result: dict[str, Any] = {
        "cap_fit_profile": CAP_FIT_PROFILE, "visual_verified": False, "flags": [],
    }
    if not isinstance(alpha_bbox, (list, tuple)) or len(alpha_bbox) != 4:
        result["flags"].append("missing_alpha_bbox")
        return result
    left, top, right, bottom = alpha_bbox
    if right <= left or bottom <= top:
        result["flags"].append("invalid_alpha_bbox")
        return result
    ratio = (right - left) / (bottom - top)
    result["alpha_aspect_ratio"] = round(ratio, 3)
    if ratio > MAX_ART_RATIO:
        result["flags"].append("overwide_cap_artwork_review_required")
    elif ratio < MIN_ART_RATIO:
        result["flags"].append("over_tall_cap_artwork_review_required")
    if (right - left) / 2048 < .25 or (bottom - top) / 1024 < .20:
        result["flags"].append("small_cap_artwork_review_required")
    return result
