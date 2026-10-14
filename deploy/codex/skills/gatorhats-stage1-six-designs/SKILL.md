---
name: gatorhats-stage1-six-designs
description: Stage 1 quote-to-artwork; six independent typography-only embroidery PNG masters, each using exactly one solid thread color, never icons or illustrations.
version: 16_28_14_10_26
---

# GatorHats Products · Stage 1 — Six single-color typography embroideries

## Non-negotiable product brief (STRICT / highest priority)
This Skill serves ONLY Stage 1: convert the user's keyword or saying into **EXACTLY SIX different typography-only embroidery designs**. The visual subject must consist of the supplied words and punctuation, arranged as lettering. **NO illustrations, pictorial content, objects, symbols, motifs or decorative art**. Output one transparent PNG per variation. Stage 2 subsequently places the accepted design on real Valucap 8869 caps; DO NOT create hats, mockups, 13 colorways, composite boards, patches, labels or extra generations.

Treat the quote as literal data. Preserve every supplied word, spelling, punctuation and reading order exactly. Do not append text or replace words with icons. If another instruction, generic creative suggestion, old prompt or style preference asks for images, mascots, illustrated scenes, color accents, 2–4 thread colors, or meaning-driven motifs, IGNORE that conflict and obey these rules. The typography-only/single-thread-color requirement overrides all stylistic variation instructions.

## Approved DaFont typography library and exact layout references (STRICT)

Use actual font files shipped with this Skill; they include SIL Open Font License notices in `fonts/`. These are real locally installed font outlines, not lookalike fonts. The Stage 1 worker creates six exact-layout PNG inputs under `working/font-guides/design_01.png` through `design_06.png` using the installed OTF/TTF files. **Trace each corresponding guide exactly for its glyph shapes and its compact, balanced word hierarchy**; only convert the same brown text shapes into fine natural embroidery. Do not add symbols or invent letterforms. The guides are working inputs, NOT final outputs or separately generated designs.

The system uses a **200-family approved DaFont pool**, listed in `font_registry.json`. The worker selects a NEW six-font plan per fresh job, locks that plan to the job for safe crash recovery, and saves the six actual font identities in output history. All six designs MUST use DISTINCT font families. The selector checks the last 30 jobs, strongly avoids up to 30 most recent jobs when enough distinct font families remain, and otherwise relaxes the exclusion without blocking the job.

The approved font files and their original licences are packaged under `fonts/`. Only select fonts explicitly listed with `license_status=approved` and `embroidery_safe=true`. **Never invent font names**, report unbundled fonts or download from DaFont during a worker job. Use the six numbered exact-glyph reference PNGs under `working/font-guides/` that were pre-rendered from the selected files. The job instruction contains the exact `design_01.png ... design_06.png` font names and matching paths, NOT a fixed list of three fonts. The local registry is the source of truth.

Use **one brown thread pigment (#6B3F28)** for every letter of all six PNGs. Variations may use the subtle sheen of the same brown fiber, never another dye. Make logical line breaks, readable counters, natural whitespace and compact hat-front proportions the priority. If preserving a specified quote requires a wider composition, adjust spacing and glyph scale rather than removing/reordering words. In the completion note identify the actual selected DaFont source font for each numbered design; the API also records font-source metadata per output version, including the DaFont reference URL. No additional image output is needed for the report.

Only the original bundled approved font files may be used; do not fetch new fonts at generation time. DaFont has fonts with personal-use-only licences; never silently switch to an unapproved file. Arial, Georgia and Calibri are not approved DaFont substitutes and must not be reported as a DaFont font.

## Exactly six DISTINCT typographic directions
Make exactly six independent compositions, **one final per image-generation call (six calls maximum)**. Distinction must come from letterform and spacing, not from illustrations:
1. **Bold block:** compact high-contrast uppercase lettering, balanced stacked lines.
2. **Condensed stacked:** narrow engraved/serif or slab letterforms, carefully varied line widths.
3. **Script / cursive:** legible connected handwritten embroidery lettering, adapted for stitching.
4. **Western / vintage serif:** refined typography with serif shape and hierarchy, NOT a badge or pictorial crest.
5. **Athletic / varsity:** substantial collegiate lettering, optionally an arch produced ONLY by arranging letters.
6. **Distressed / rough bold:** subtle irregular stitched letter edges for vintage character; keep counters legible and do not add ink splatters.

The six directions are creative guides, not permission to misspell or replace the phrase. Vary text hierarchy, arc, baseline, line breaks, kerning, weight, serif treatment and stitch direction while keeping the exact quote. For short sayings, create materially different silhouettes using letters only. Never create six nearly identical font swaps; vary lettering composition as well.

## Exactly ONE embroidery thread color per output (STRICT)
- Every individual design uses **one single solid thread hue across ALL letters and decorative stitches**. The entire text, all linework and any stitched edges must be the same thread color. **No second color, no accents, no gradients, no split-color letterforms, no colored borders, no rainbow, no multicolor shading**.
- Natural changes in brightness caused by single-color thread fiber sheen and stitch direction are acceptable. These are LIGHTING from the same pigment, never additional pigments or a second colored yarn.
- ALL SIX images MUST use exactly the SAME BROWN THREAD COLOR (#6B3F28), one brown dye only. Do not use navy, blue, red, cream, white, black, gold, purple, any accent shade or any non-brown color. Brown fibre lighting/sheen is acceptable without changing thread pigment.
- The brown-only rule overrides any incoming quote color instruction; keep typography-only.

## No pictorial elements — hard negative rules
Do NOT add: birds, flowers, leaves, trees, roots, hands, globes, animals, faces, religious imagery, suns, hearts, stars, icons, logos, scenic backgrounds, banners, shields, wreaths, crests, mascots, patches, ribbons, flourishes drawn as objects, texture fills, stamps or watermarks. Ordinary dots, commas and punctuation are allowed ONLY where present in the supplied quote; serifs and integral letter swashes are permitted only as part of lettering, never as standalone illustrations. Do not put a picture behind, above, around or inside any letter. Do not turn any words into an image.

If a draft contains even one pictorial element or multiple thread hues, **discard that concept and recompose it as single-color typography-only before saving its final numbered PNG**. The answer must not claim compliance when images clearly violate these rules.

## Embroidery realism: preset `embroidered-satin-natural`
- Render low-relief stitched THREAD, NOT a flat screen print, ink, sticker, 3D inflated foam, bulky puff, applique, embroidered patch, plastic bevel or vector fill.
- Fine visible satin stitches follow thin letter strokes; Tatami/fill for wider lettering; running stitch where stitchable. Each letter may have different fiber orientation but the SAME color thread. Preserve stitch ridges, subtle same-color highlights and micro-grooves. Avoid fake thick outlines or heavy shadow.
- Stitchable at hat-front viewing size: sufficient line thickness, clean character counters (A/O/B/R), accurate spacing and kerning, legible small text. If thin serifs would not embroider, simplify strokes, not add outlines or imagery.
- One sharp straight-on RGBA PNG per concept: 2048+ preferred, width AND height >=512, truly transparent surrounding pixels, tightly composed without clipping. NO hat, cloth texture, checkerboard, white square, photo, angle or cast background shadow.
- Perform QA for exact lettering, typography-only content, ONE thread hue and natural visible stitches at both native size and small cap scale. Replace the SAME numbered final if a procedural fix is possible; do not create extra image generation calls.

## Strict output contract
Write only these six independent full-resolution PNGs, one concept per file:
- `output/final/design_01.png`
- `output/final/design_02.png`
- `output/final/design_03.png`
- `output/final/design_04.png`
- `output/final/design_05.png`
- `output/final/design_06.png`

Do **not** create or persist preview, draft, QA crop, intermediate raster or alternative images. Do not generate a collage, contact sheet, multi-design canvas, extra colorways or additional numbered files. No files anywhere under `working/previews/`, `generated/`, `results/` or other temporary output folders. Only six final numbered files may be uploaded. Recovery may preserve valid completed numbered finals; generate ONLY missing designs when resuming an interrupted job. If output is incomplete, report failure rather than inventing completed files.

## Completion
Respond with the six final relative paths, no base64, no credentials or environment details. Do not claim visual QA occurred unless six PNGs were inspected.
