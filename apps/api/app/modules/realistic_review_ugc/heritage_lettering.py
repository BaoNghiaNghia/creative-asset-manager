"""Original cap-first Heritage lettering presets for nine-stage artwork jobs.

Only structural art direction is reusable. Never copy source text, glyph outlines,
brand marks or reference pixels. Existing jobs pin their chosen style in payload.
"""
from __future__ import annotations

STYLE_MODES = ("balanced", "heritage", "experimental")
AUTO_STYLE = "auto"
# Update these two constants for the next rollout. Preserve every existing
# archive entry so in-flight Retry never silently adopts the new art direction.
DEFAULT_DESIGN_STYLE = "heritage"
HERITAGE_VERSION = "heritage-lettering-h1"
STYLE_POLICY_VERSION = "stage1-heritage-default-h1"
HERITAGE_LAYOUTS = (
    "Classic Heritage Serif: compact two-line traditional serif, deliberate optical kerning, strong graceful terminals; mostly one thread hue.",
    "Ornamental Serif: expressive custom serif letter anatomy, one restrained swash or shaped terminal; never thin filigree.",
    "Bold Script and Microtype: one dominant robust connected script line with short readable supporting uppercase lettering, not hairline calligraphy.",
    "Editorial Script Overlay: solid serif anchors and one short script accent with extremely limited overlap, all original letters remain readable.",
    "Signature Heritage: fluent but sturdy script above a stable traditional serif baseline, one unified embroidered wordmark.",
    "Collegiate Block: bold athletic or collegiate block lettering with generous counters and balanced rhythm, no fake plastic extrusion.",
    "Condensed Editorial: carefully condensed display serif, strong ascenders and readable word grouping in two compact rows.",
    "Arched Heritage: shallow crown-compatible upper line anchored by a robust straight lower line, never a deep arch or banner.",
    "Creative Heritage Hybrid: meaningful combination of two contrasting letter families with a coherent single reading order and restrained custom glyph detail.",
)

def heritage_fit_advisory(alpha_bbox: object, *, version: str | None = None) -> dict:
    """Geometry-only warning; not glyph accuracy or embroidery certification."""
    pinned = version or "heritage-lettering-h1"
    if pinned not in HERITAGE_PACK_ARCHIVE:
        raise ValueError("Unsupported pinned Heritage style version")
    report = {"profile": pinned, "verified_lettering": False,
              "verified_embroidery": False, "flags": []}
    if not isinstance(alpha_bbox, (list, tuple)) or len(alpha_bbox) != 4:
        report["flags"].append("bbox_unavailable")
        return report
    left, top, right, bottom = alpha_bbox
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in alpha_bbox):
        report["flags"].append("bbox_invalid")
        return report
    if right <= left or bottom <= top:
        report["flags"].append("bbox_invalid")
        return report
    ratio = (right - left) / (bottom - top)
    report["ink_aspect_ratio"] = round(ratio, 3)
    if ratio < 1.4:
        report["flags"].append("heritage_layout_too_tall_review")
    if ratio > 2.6:
        report["flags"].append("heritage_layout_too_wide_review")
    if left < 40 or right > 2008 or top < 25 or bottom > 999:
        report["flags"].append("unsafe_canvas_margin_review")
    return report


# Append a new immutable pack and update the ACTIVE pointer when shipping H2.
# Never edit either H1 layouts or shared prompt text in place: old attempts
# reuse their versioned pack when a user selects Retry.
HERITAGE_SHARED_GUIDANCE_H1 = (
    " TYPOGRAPHY MUST BE THE HERO. Prefer sophisticated original serif, "
    "hand-lettered script, signature and collegiate construction; never force "
    "illustrations, objects, jokes, logos, badges or ornament. One or two "
    "thread colors are usually enough; monochrome is excellent. Keep all "
    "supporting words above physical stitch readability limits; omit optional "
    "decorative microtype instead of making it illegible. Swashes must be "
    "stitched shapes with open counters; overlaps must not cover letters. "
    "Preserve source text exactly and recompose it, NEVER trace or imitate "
    "the glyph outlines of a supplied commercial reference."
)
HERITAGE_PACK_ARCHIVE = {
    "heritage-lettering-h1": {
        "layouts": HERITAGE_LAYOUTS,
        "shared_guidance": HERITAGE_SHARED_GUIDANCE_H1,
    },
}
STYLE_VERSIONS = {
    "balanced": "balanced-v1",
    "experimental": "experimental-v1",
}


def validate_style_mode(value: str) -> str:
    if value not in STYLE_MODES:
        raise ValueError("Unsupported Stage 1 design style")
    return value


def resolve_style_policy(requested: str | None = AUTO_STYLE) -> tuple[str, str]:
    """Resolve the current default only when creating a fresh attempt.

    Generate and Regenerate call this once at queue time; Retry never calls it.
    Explicit operator overrides remain possible, but a missing/auto request
    always follows the current server policy independent of client version.
    """
    mode = DEFAULT_DESIGN_STYLE if requested in (None, AUTO_STYLE) else validate_style_mode(requested)
    version = HERITAGE_VERSION if mode == "heritage" else STYLE_VERSIONS[mode]
    return mode, version


def verify_style_snapshot(mode: str, version: str | None) -> None:
    validate_style_mode(mode)
    # An absent version is a historical attempt, pinned to its original H1
    # or non-Heritage behavior rather than any future active default.
    if version is None:
        return
    if mode == "heritage":
        if version not in HERITAGE_PACK_ARCHIVE:
            raise ValueError("Unsupported pinned Heritage style version")
    elif version != STYLE_VERSIONS[mode]:
        raise ValueError("Unsupported pinned Stage 1 style version")


def style_guidance(mode: str, slot: int, *, version: str | None = None) -> str:
    verify_style_snapshot(mode, version)
    if not 1 <= slot <= 9:
        raise ValueError("Invalid Heritage slot")
    if mode == "balanced":
        return ""
    if mode == "experimental":
        return (
            "EXPERIMENTAL MODE: vary custom letter anatomy, curvature, hierarchy, "
            "and meaningful rhythm first; a text-only solution remains valid. "
            "Never sacrifice exact spelling, cap-front footprint or stitch readability."
        )
    # Legacy snapshots with no version were generated under H1.
    pinned = version or "heritage-lettering-h1"
    pack = HERITAGE_PACK_ARCHIVE[pinned]
    return (
        f"HERITAGE STYLE PACK {pinned}; SLOT {slot:02d}: "
        + pack["layouts"][slot - 1]
        + pack["shared_guidance"]
    )
