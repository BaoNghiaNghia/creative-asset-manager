"""Mandatory direct-thread appearance for future GatorHats Products Stage 2 generations.

These instructions live in the *job prompt*, not the installed SKILL.md, so
tenant-uploaded Skills and their pinned SHA/version remain unmodified. The
official stock, source artwork and per-slot camera directions keep priority.

A visual inspection instruction is not a computer-vision QA classifier; the
worker's deterministic output validation remains unchanged.
"""
from __future__ import annotations

EMBROIDERY_REALISM_VERSION = "canonical-design-direct-thread-v4"

# Covers 4 independent ecommerce outputs, three Multi-hat scenes, three UGC
# scenes and 12 official Products colorways (one policy for the colorway slot).
_SLOT5 = ("prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3")
_SLOT6 = ("prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3")

_SHARED_THREAD = (
    "CAM PRODUCTS STAGE 2 - DIRECT EMBROIDERY MATERIAL STANDARD "
    f"[{EMBROIDERY_REALISM_VERSION}]: "
    "Treat the current source artwork / design image as the exact ARTWORK identity and the official "
    "Valucap 8869 stock as the exact HAT geometry and fabric-color authority. "
    "CANONICAL DESIGN RULE: the exact source typography, font style, lettering "
    "geometry, proportions, line hierarchy, composition and individual thread "
    "colors are immutable. The original transparent design is the identity "
    "authority. NEVER reinterpret or re-typeset it from a fabric photograph. "
    "Any on-fabric embroidery photo is ONLY a material-quality reference, "
    "not a glyph, background, palette or cloth panel to paste onto the cap. "
    "Reconstruct stitches directly on the official stock cap's weave, lighting "
    "and curvature while keeping the canonical design unchanged. "
    "Any pre-composited or previously generated embroidered-front reference is "
    "ONLY evidence for letter placement, scale, and perspective: do not copy its "
    "flat overlay, fake thick relief, hard outline, or artificial thread texture. "
    "Reconstruct physical stitches on the fabric. "
    "Target the photographed REAL-THREAD appearance of subtle cream embroidery "
    "on a burgundy corduroy cap (the user's quality benchmark): low stitch height, "
    "gentle cloth interaction, organic filament detail and soft daylight. "
    "This is a MATERIAL reference only: never borrow its cream/red palette, "
    "corduroy fabric, words, logo, hat construction or lifestyle background. "
    "Use genuine LOW-PROFILE direct machine embroidery stitched into the crown: "
    "fine individual thread filaments, restrained satin stitches following narrow "
    "letter strokes, fine directional tatami/fill for wider areas, delicate running "
    "stitches for small details, natural stitch-direction changes along curved glyphs, "
    "moderate stitch density and tiny non-uniform fiber highlights. "
    "Threads should sit close to the underlying weave with a shallow believable "
    "lift, slight local fabric tension and soft micro-shadow directly where fibers "
    "meet the cloth; show the original textile structure instead of a raised solid "
    "layer. Keep thread sheen subdued, edges slightly organic but legible, and "
    "scale stitch grooves as fine threads rather than broad repeated diagonal "
    "ridges. Preserve all readable characters, type silhouettes, layout, relative "
    "size, front placement and original independent thread colors exactly; never "
    "invent, mirror, recolor to the brim, thicken or re-typeset the design. "
    "This physical material standard overrides any contradictory 3D, emboss, "
    "puffy, thick, shiny or hyper-crisp style phrases in an uploaded Skill, but "
    "does NOT override its photo composition or number of images. "
    "STRICT NEGATIVE: NO puff/3D foam embroidery, no bulky padded letters, "
    "extruded or embossed text, deep bevel, thick rubber/plastic/vinyl, "
    "silicone print, glossy sticker, flat printed logo, raised applique patch, "
    "uniform coarse parallel ridges, dark cutout halo, black stroke outlines, "
    "oversharpening, dramatic hard contact shadow, fake lighting or floating "
    "letters. Do not add embroidery to any surface where official stock has "
    "no embroidery. "
)

_FRONT_QA = (
    "PRE-SAVE VISUAL QUALITY CHECK: compare at close-up against authentic real "
    "embroidered thread on cloth, not a digital emboss effect. Reject the candidate "
    "as an output if letters resemble a thick plastic/rubber mold, an appliqued "
    "patch, printed decal, giant regular ribs, or a dark 3D extrusion; also reject "
    "text errors, stock-color/geometry errors, invented embroidery, or visibly "
    "floating stitches. Correct that *same* image before placing final.png. "
    "Do not claim that an image passed if these failures remain; do not submit "
    "rejected alternatives as successful final assets. Preserve original "
    "completed jobs and never alter existing stored outputs. "
)
_NO_FRONT = (
    "When the front embroidery is completely hidden in the selected camera "
    "angle, do not invent or reveal any front text or fabricate stitching "
    "behind the crown; keep blank back panels blank. Preserve only an official "
    "manufacturer side logo already visible in stock and render its small "
    "stitches with the same low-profile, fine-thread material. "
)

_SLOT_DIRECTIONS = {
    "prod_s2_ec1": (
        "SLOT 1 / FRONT: render the source lettering inside the existing "
        "calibrated front embroidery rectangle. A physically attached, thin "
        "embroidered finish is more important than artificially deep grooves "
        "or hard-edged oversized typography. "
    ),
    "prod_s2_ec2": (
        "SLOT 2 / FRONT-RIGHT 3/4: wrap and foreshorten the same fine embroidery "
        "over the curved front panel; use the completed Front for text and "
        "placement only, never for thick relief. Do not duplicate front art "
        "on the lateral panel. "
    ),
    "prod_s2_ec3": (
        "SLOT 3 / TRUE RIGHT SIDE: show only the small part of front art that "
        "would physically peek around the crown, with correct occlusion. "
        "The factory GatorHats side wordmark follows the official stock; "
        "keep it fine, low-relief and realistically small, no new text. "
    ),
    "prod_s2_ec4": (
        "SLOT 4 / BACK: this shot requires a faithful UNEMBROIDERED rear crown. "
        "No front lettering on the back and no new rear logo. Only any factory "
        "mark genuinely visible at the far side edge may have fine stitches. "
        + _NO_FRONT
    ),
    "prod_s2_col": (
        "SLOT 7 / TWELVE STOCK COLORS: same exact design and subtle embroidery "
        "finish across every official Natural-series cap color. Only the "
        "stock crown/bill color changes; preserve all independent source "
        "thread pigments, satin direction, logo scale, and front placement. "
        "Never remap colored embroidery to brown/sepia or to the hat visor. "
    ),
}

for _stage in _SLOT5:
    _SLOT_DIRECTIONS[_stage] = (
        "SLOT 5 / MULTI-HAT: each physically separate cap that visibly exposes "
        "its front embroidery must have the same authentic fine, low-profile "
        "thread finish, regardless of color or camera distance. Preserve the "
        "scene's exact required number of hats, palette, approved angles "
        "and independent composition. No copy-pasted planar front logo. "
    )
for _stage in _SLOT6:
    _SLOT_DIRECTIONS[_stage] = (
        "SLOT 6 / NATURAL UGC: make embroidery behave like real stitching on "
        "a worn, slightly curved five-panel cap under the scene's actual "
        "daylight and camera distance. Let thread detail resolve naturally "
        "at selfie scale; do not use an artificially sharp, glowing or raised "
        "logo to force readability. Preserve the pinned lifestyle composition, "
        "actual wearer interaction and source artwork. "
    )


def embroidery_prompt_for_product_slot(stage: str) -> str:
    """Mandatory output appearance, no change to stored Skill bundles or DB rows."""
    try:
        direction = _SLOT_DIRECTIONS[stage]
    except KeyError as exc:
        raise ValueError(f"Unsupported GatorHats Products slot: {stage}") from exc
    return " ".join((_SHARED_THREAD, direction, _FRONT_QA))


def covered_product_slot_stages() -> frozenset[str]:
    return frozenset(_SLOT_DIRECTIONS)
