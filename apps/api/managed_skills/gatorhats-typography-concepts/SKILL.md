---
name: gatorhats-typography-concepts
description: Create six independent realistic satin-embroidered lettering concepts from a quote for GatorHats Products Stage 1. Use bundled officially licensed fonts, faithful royal-blue and red script compositions, and the CAM deterministic renderer for automatic 2048×1024 transparent PNG delivery.
---

# GatorHats Typography Concepts · Stage 1 (Reference Collection)

Input: the **literal quote** (up to 55 printable characters). No mockups, design upload, external fonts, image generation service or human approval step is required.

## Final output contract

- Publish **six independent 2048 × 1024 PNGs with RGBA transparency** at `output/final/design_01.png` to `design_06.png` plus `output/manifest.json`.
- Preserve all words, punctuation and case-independent reading order. No illustrations, pictograms, outlines of caps, patches or decorative imagery.
- Use royal-blue (#2852AA) thread with subtle near-navy valleys. Only concept 03 may add a red (#B72B20) script accent.
- Work from the Google Fonts **official OFL source binaries bundled under `fonts/`**, verified against `font_sources.json` and accompanied by `licenses/`. Do not use legacy external font registries or random font selection.
- Finish **inside the exact letter alpha mask** with diagonal satin rows, highlights, dark needle valleys and subtle stitched edge relief. Never leave glyphs as a smooth flat print.
- Do **not** create or persist preview drafts as final outputs; generate only the six independent PNG masters.
- The CAM-owned `stage1_typography_renderer.py` creates the six layouts and `stage1_stitch_texture.py` applies the embroidery texture deterministically. Fail closed on invalid font hashes, missing licenses or incomplete output.
- Keep existing completed Drive assets and version history. Regenerate a keyword only when requested.

## Composition recipes from Concept output(3).zip

1. **Royal Blue Block Stack:** two stacked lines of bold blue capitals; short first line, wide heavier second (CHAOS / COORDINATOR).
2. **Contrasting Condensed Stack:** tall condensed upper line and oversized solid-blue lower line (STATE / OF MIND).
3. **Red Script Over Blue Block:** red cursive headline above, slightly overlapping large slanted blue capital support (State of / MIND; Hamburger / HELP ME).
4. **Serif Arch With Script:** clearly curved wide blue uppercase headline above centered handwritten blue subtitle (STATE / of Mind; CHAOS / Coordinator).
5. **Blue Script Plus Small Caps:** large flowing blue script in upper half with compact block capitals below (State / OF MIND).
6. **Groovy Letter Stack:** chunky blue retro serif letters on two or three staggered lines, expressive arrangement but correct character order (Y'ALL / NEED / JESUS).

See `references/STYLE_REFERENCE.md` for example-driven line splitting, visual balance and QA checks. Every final must be legible at a cap-front embroidery size. A white backdrop is only for preview and must not be baked into the transparent PNG.
