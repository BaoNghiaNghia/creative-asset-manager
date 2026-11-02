# CAM Products Slot 7 — Visor-Matched Embroidery Accent V1

**Scope:** 12 official Natural-series colorways only. Reviews Stage 2, Products Slots 1–6, previously queued jobs, and completed output files remain unchanged.

## Mechanism

- Reuse one saved Slot 1 photographic Embroidery Reference and the official manufacturer stock for every visor.
- Unsupervised computer-vision / machine learning selects a distinct yarn color region (LAB k-means), or a complete lettering/motif component if artwork is monochrome. The selector does not generate or retype artwork and costs zero ImageGen calls.
- Cap accent at 30% of visible embroidered ink pixels (not the transparent canvas). Never cut a glyph merely to reach 30%.
- Freeze one grayscale PNG mask per tenant + design + source revision + saved reference + algorithm version. A PostgreSQL row lock on the shared source plan serializes the first selection across 12 concurrent color jobs. All later colors reuse the mask.
- Apply one of 12 curated thread tones matching each visor. Adapt brightness only if needed to ensure contrast against crown and original thread. Keep source thread RGB outside mask, source geometry, thread lighting/texture, alpha, visor, stock and side logo unchanged.
- When no complete safe accent exists, cache an empty mask and record skipped_no_safe_region rather than corrupting a word.

## Validation and metadata

Validate pinned reference SHA, layer fingerprint, mask SHA, image dimensions, max 30% coverage, unchanged non-accent RGB, unchanged alpha, and a visible recolor. Require >=2.5:1 WCAG-style luminance contrast to the original crown and >=19 OpenCV-LAB units to dominant unselected thread. Fail unsafe outputs instead of publishing.

Each output version records the algorithm/mask fingerprint, selection ratio, method, color key, effective thread RGB, contrast and cache-hit in font_info_json.thread_colorway_pipeline.visor_accent, along with 0 native ImageGen passes.

Additive migration 0192_products_visor_accent_v1 creates only the tenant-scoped immutable mask cache. Existing v2 jobs retain historical behavior; new/explicitly retried Products Slot 7 jobs use v3-visor-accent. No mass backfill or automatic regeneration. Reviews jobs continue using the independent provider path.

## Production

Run unit/worker/UI tests and migrations, release main via reviewed source commit followed by a reproducible frontend build artifact commit, deploy backend/frontend using standard scripts and verify health. Preserve previous release for rollback and never drop populated mask/output history.
