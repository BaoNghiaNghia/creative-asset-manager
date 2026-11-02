"""Single source of truth for NEW Stage 1 native ImageGen stitch material."""

MATERIAL_PROFILE = "natural-low-relief-satin-v1"

MATERIAL_INSTRUCTIONS = (
    "NATURAL EMBROIDERY MATERIAL PROFILE (authoritative): Render as real "
    "low-relief machine embroidery, not extruded typography, resin, clay, "
    "foam, rubber, glossy plastic, metallic bevel or a simulated sticker. "
    "Use directional satin columns where stitch length is viable and realistic "
    "tatami/fill stitches on larger areas, with restrained running stitch "
    "for meaningful small accents. Show subtle fiber grooves and gentle "
    "edge relief, not artificial cast shadows. Give each letter a clean "
    "single-color or multicolor filled stitch silhouette WITHOUT a separately "
    "colored perimeter outline, halo, double contour or patch border. "
    "Preserve readable counters, spacing, serifs and strokes at small cap-front "
    "scale. Choose 1-4 coordinated thread colors according to meaning; "
    "monochrome is acceptable when it makes a clear compelling design. "
    "No forced color diversity, decorative icons, text deformation, heavy "
    "layering, tiny thread art, or shiny 3D puff unless explicitly requested. "
    "The exact words must remain legible. Texture is inside each glyph. "
)

# V4 remains untouched for pinned in-flight jobs. New cap-first V5 is a
# distinct material contract that learns from the user's prior handmade
# satin sample without embedding or copying its source image.
CAP_MATERIAL_PROFILE = "true-satin-embroidered-lettering-v2"
CAP_MATERIAL_INSTRUCTIONS = (
    "PHYSICAL CAP EMBROIDERY MATERIAL (authoritative): Genuine machine-embroidered "
    "letterforms composed from visible directional satin threads and tight "
    "tatami stitches on larger fills; fine thread valleys and natural specular "
    "ridges follow each glyph's curvature. Tactile but LOW-RELIEF: small natural "
    "edge thickness, never a molded 3D object, foam puff, carved plastic, "
    "chrome, polymer, printed graphic, glossy bevel or vector-styled shading. "
    "Letter shapes have intentionally handcrafted western/retro/script character "
    "when the meaning calls for it, but each small counter stays open. "
    "Use coherent 2-4 complementary thread hues when beneficial, including "
    "a warm main hue and a controlled accent for western sayings; monochrome "
    "remains valid if meaningful. Allow a single delicate connecting satin "
    "flourish or tiny star only when it visually unifies the letter grouping "
    "without stealing vertical space. Never force ornaments or shadow layers. "
    "No thick contrasting contour, patch border, double-outline, white sticker "
    "edge or separated cast shadow around any letter. Do not copy the old "
    "font's shape verbatim from a third-party reference. Stitch texture must "
    "look woven INTO each glyph, not placed over a smooth rubber surface. "
)
