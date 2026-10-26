---
name: gatorhats-typography-concepts
description: Create six independent realistic satin-embroidered lettering concepts from a quote for GatorHats Products Stage 1. Use only Google Fonts OFL fonts with verified provenance, reference-driven layouts, and clearly visible raised satin embroidery. Generate six 2048×1024 opaque RGB PNG photographs of embroidery on cotton twill, never flat-font artwork.
---

# GatorHats Typography Concepts · Stage 1 (Reference Collection)

Input: the **literal quote** (up to 55 printable characters). No mockups, design upload, external fonts, image generation service or human approval step is required.

## Final output contract

- Publish **six independent 2048 × 1024 opaque RGB PNGs showing realistic embroidery stitched into neutral beige woven twill** at `output/final/design_01.png` to `design_06.png` plus `output/manifest.json`.
- Preserve all words, punctuation and case-independent reading order. No illustrations, pictograms, outlines of caps, patches or decorative imagery.
- Use royal-blue (#2852AA) thread with subtle near-navy valleys. Only concept 03 may add a red (#B72B20) script accent.
- **Google Fonts is the sole font catalog.** Read the verified OFL fonts shipped with this Skill; use `font_sources.json` as the authoritative provenance manifest and the accompanying `licenses/`. Do not use DaFont, retired font registries, per-user font uploads, font URL scraping, cached legacy font selections, platform-default fallbacks, or random font choice.
- Preserve exact letter geometry; render local transverse satin stitches aligned to individual glyph strokes, subtle thread highlights and stitch valleys, fine needle grooves, slight fabric tension and restrained contact shadows. Reject globally repeated diagonal patterns and puffy/plastic embroidery. Embroidery must remain visible when a 2048px master is reduced to a 180–220px UI preview. Reject smooth flat printed text and excessively small lettering.
- Do **not** create or persist preview drafts as final outputs; generate only the six independent PNG masters.
- The CAM-owned `stage1_typography_renderer.py` creates the six layouts, `stage1_stitch_texture.py` shades thread geometry, and `stage1_fabric.py` generates opaque woven-fabric photos embedding checksum-verified source geometry for Stage 2. Never publish a standalone transparent artwork. Fail closed on invalid font hashes, missing licenses or incomplete output.
- Keep existing completed Drive assets and version history. Regenerate a keyword only when requested.

## Composition recipes from Concept output(3).zip

1. **Royal Blue Block Stack:** two stacked lines of bold blue capitals; short first line, wide heavier second (CHAOS / COORDINATOR).
2. **Contrasting Condensed Stack:** tall condensed upper line and oversized solid-blue lower line (STATE / OF MIND).
3. **Red Script Over Blue Block:** red cursive headline above, slightly overlapping large slanted blue capital support (State of / MIND; Hamburger / HELP ME).
4. **Serif Arch With Script:** clearly curved wide blue uppercase headline above centered handwritten blue subtitle (STATE / of Mind; CHAOS / Coordinator).
5. **Blue Script Plus Small Caps:** large flowing blue script in upper half with compact block capitals below (State / OF MIND).
6. **Groovy Letter Stack:** chunky blue retro serif letters on two or three staggered lines, expressive arrangement but correct character order (Y'ALL / NEED / JESUS).

See `references/STYLE_REFERENCE.md` for example-driven line splitting, visual balance and QA checks. Every final must be legible at a cap-front embroidery size. The neutral fabric is the opaque Stage 1 preview; downstream hats must use only verified thread geometry, never paste the beige fabric onto another stock color.