---
name: gatorhats-typography-concepts
description: GatorHats Products Stage 1: create six transparent typography-only PNG embroidery design concepts from a keyword or quote. Use the deterministic CAM six-concept renderer with verified license-cleared font assets; never ask for preview uploads or user concept selection during a background Stage 1 job.
---

# GatorHats Typography Concepts · CAM Stage 1

Stage 1 must publish **exactly six** independent, nonempty transparent RGBA PNG files named `output/final/design_01.png` ... `design_06.png`, 2048×1024, with an `output/manifest.json` containing hashes and names.

## Contract

- Input is one literal slogan/keyword, max 55 characters for legible embroidery. No images are required.
- Render C1 Varsity Stack, C2 Block Stack, C3 Script Lockup 2-Color, C4 Club Arch, C5 Script Overlap, C6 Groovy Wavy Stack from bundled licensed fonts using the application-owned `stage1_typography_renderer`.
- Use royal blue #3558B5 as main thread and red #AC211C only as C3 accent, matching the original user typography concept palette.
- Do not produce hats, product photos, extra symbols, previews, or image collages as final deliverables.
- Render all six locally; do not invoke Codex/imagegen or require manual A/B uploads. The CAM worker uploads only verified final files. Missing fonts, overflow, empty or duplicate images must fail rather than falsely succeeding.
- Completed historical jobs/Drive assets remain intact; new outputs are persisted through the existing transactional Stage 1 upload and version ledger.

Original uploaded GatorHats Typography Concepts had an interactive approval workflow and sometimes skipped C3/C5. CAM enforces six fixed output slots and uses fallback recipe arrangements for short quotes, without pretending an incomplete batch succeeded.
