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
HERITAGE_VERSION = "heritage-lettering-h2"
STYLE_POLICY_VERSION = "stage1-heritage-cap-diversity-h2"
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

# H2 is a new immutable policy for fresh Generate and Regenerate attempts only.
# Its nine archetypes are inspired by structural qualities of real cap embroidery:
# customized heritage serifs, compact varsity, ornamental lettering, and
# short script/serif overlays. Do NOT trace or reproduce reference designs.
HERITAGE_LAYOUTS_H2 = (
    "01 / CLASSIC HERITAGE MONUMENT: make an elegant customized old-style serif "
    "wordmark with exactly two compact tiers. An oversized keyword anchors the "
    "composition, with optical kerning, open counters and one graceful terminal. "
    "Text-only, one-color, no shadows, bevels or generic default font.",
    "02 / EXPRESSIVE EDITORIAL SERIF: dramatic high-contrast letter proportions "
    "and an offset small uppercase connector. Organize a distinct large/short/large "
    "rhythm in two or three balanced bands; never the same scale per line.",
    "03 / SCRIPT OVER SERIF: one SHORT genuinely connected, stout embroidered "
    "script phrase crosses a larger confident serif phrase at a carefully chosen "
    "gap, without hiding any glyph. Limit overlap and never make the entire "
    "long quote one thin script line.",
    "04 / ORNAMENTAL HERITAGE: custom flared/curved serif terminals, an original "
    "single swash emerging FROM a letter, not a separate illustration. Balance "
    "bold stable lower lettering with a highly crafted short upper phrase.",
    "05 / VINTAGE VARSITY: an athletic collegiate block or slab letter silhouette "
    "with carefully spaced strong capitals, honest matte satin fills and generous "
    "negative space. Compact two-row form; absolutely no extrusion or outline.",
    "06 / SHALLOW ARCH EDITORIAL: crown-compatible shallow curved upper phrase "
    "over an oversized straight lower phrase, with deliberately different "
    "serif proportions and letter spacing; no badge, ribbon or deep rainbow arch.",
    "07 / SIGNATURE LOCKUP: short connected signature-style script as the dominant "
    "human rhythm, supported by robust small uppercase serif/microtype that "
    "still stitches legibly at cap scale. Deliberate two-family contrast.",
    "08 / CONDENSED BOOKISH EDITORIAL: unusually tall but stitch-thick custom "
    "condensed serif characters in a wide, LOW two-row lockup with powerful "
    "long/short text rhythm; no automatic symmetrical repeated font.",
    "09 / PREMIUM HERITAGE HYBRID: one original custom ligature or carefully "
    "balanced glyph interlock, plus contrasting slab/oldstyle letter families. "
    "Use a unique hierarchy and optically adjusted baseline, not another "
    "two-line gold-and-brown serif variation.",
)
HERITAGE_COLORS_H2 = (
    "deep forest-green monochrome, as on minimal cream/green heritage caps",
    "navy with a restrained ochre script/connector accent",
    "warm ivory serif with subtle dark-ink script accent",
    "copper-red single-color satin, avoid faux metallic gold",
    "cream lettering with a restrained dark navy second color",
    "brick-red with small warm-ivory contrast",
    "dark ink signature with a muted warm-rust microtype accent",
    "forest-green monochrome or dark-ink monochrome",
    "oxblood with a muted cream second thread",
)
HERITAGE_SHARED_GUIDANCE_H2 = (
    " H2 CAP-FIRST LETTERING DIVERSITY CONTRACT: each numbered slot is a "
    "separate typographic ARCHETYPE, not the same two-line serif with a new "
    "font, gradient or color. Use the requested line-break architecture and "
    "hierarchy as the controlling plan over older Creative Brief variant prose, "
    "which is ONLY semantic inspiration. Preserve ALL words, spelling, "
    "punctuation and reading order. Borrow only structural principles from "
    "reference hats: custom letter anatomy, editorial scale, athletic blocks, "
    "fluent sturdy script, single restrained swash and readable microtype. "
    "Never trace another brand's glyphs, text, logo or reference pixels. "
    "TYPOGRAPHY IS THE DESIGN. NO forced icons, scenes, stickers, badges, "
    "literal jokes, graphical motifs, gold bevels, fake 3D or shadows. "
    "Use one dominant thread hue and at most one complementary hue unless "
    "meaning clearly requires otherwise. NO multi-color outline around letters. "
    "Script/serif overlaps must not cover words and must use real stitch-width "
    "strokes. Microtype is permitted only when actually legible on the front "
    "of a 5-panel cap; enlarge it or integrate it into a main row if not. "
    "Maintain one compact 1.5–2.5:1 cap-front embroidered silhouette. "
    "If previous independent outputs might use a similar arrangement, choose "
    "this slot's distinctive baseline geometry, custom terminals, and "
    "dominant/secondary scale instead. DO NOT copy the appearance of the "
    "other eight designs in the batch."
)
HERITAGE_PACK_ARCHIVE["heritage-lettering-h2"] = {
    "layouts": HERITAGE_LAYOUTS_H2,
    "shared_guidance": HERITAGE_SHARED_GUIDANCE_H2,
}


def heritage_h2_composition(phrase: str, slot: int) -> dict:
    """Meaning-preserving line groups and a unique visual DNA for each H2 slot.

    All groups are contiguous slices of the supplied source words. The AI may
    adjust line breaks for visual fit but may never delete or reorder words.
    """
    if not isinstance(phrase, str) or not phrase.strip() or not 1 <= slot <= 9:
        raise ValueError("Invalid H2 cap lettering input")
    words = phrase.split()
    if len(words) == 1:
        lines = ((words[0],),) * 4
    else:
        n = len(words)
        balanced = min(
            range(1, n),
            key=lambda i: (abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))), i),
        )
        anchor = n - 1
        first = max(1, n // 2)
        split_three = ((max(1, n // 2), n - 1) if n >= 4 else None)
        balanced_lines = (" ".join(words[:balanced]), " ".join(words[balanced:]))
        anchor_lines = (" ".join(words[:anchor]), " ".join(words[anchor:]))
        first_lines = (" ".join(words[:first]), " ".join(words[first:]))
        if split_three and split_three[0] < split_three[1]:
            i, j = split_three
            expressive_lines = (
                " ".join(words[:i]), " ".join(words[i:j]), " ".join(words[j:])
            )
        else:
            expressive_lines = balanced_lines
        lines = (balanced_lines, anchor_lines, first_lines, expressive_lines)
    # A script component must be a short contiguous part of the actual phrase.
    script = next(
        (part for part in (lines[2][0], lines[0][0], words[0])
         if len(part) <= 14), ""
    )
    configurations = (
        (0, "oversized elegant serif lower anchor, smaller upper tier; two rows"),
        (3, "large/very-small/large editorial rhythm; connector never hairline"),
        (2, "stout connected short script over broad stable lower serif phrase"),
        (1, "one custom swash inside the leading serif capital; anchor on bottom"),
        (2, "balanced bold collegiate block with pronounced open counters"),
        (1, "shallow curved upper row with a strong straight second row"),
        (3, "short strong signature above concise uppercase supporting tiers"),
        (0, "tall condensed sturdy serifs in two wide low lines, no tall stack"),
        (3, "custom ligature or selective interlock in a unique three-tier lockup"),
    )
    line_index, hierarchy = configurations[slot - 1]
    return {
        "lettering_archetype": HERITAGE_LAYOUTS_H2[slot - 1],
        "advisory_line_groups": list(lines[line_index]),
        "visual_hierarchy": hierarchy,
        "short_script_candidate": script if slot in (3, 7) else None,
        "dominant_content_word_candidate": max(words, key=len),
        "thread_palette": HERITAGE_COLORS_H2[slot - 1],
        "source_word_order_intact": " ".join(words) == " ".join(lines[line_index]),
        "no_reference_artwork_copying": True,
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
