# Reviews Stage 3 / Stage 4 embroidery groups

## Canonical rule (2026-10-14)

Both Stage 3 Pinterest references and Stage 4 image generation get their rows from
`GET /api/v1/realistic-review-ugc/source-plans`, which uses
`group_source_plan_rows` in `embroidery_groups.py`. Stage 4 job-history
and duplicate-reference checking also resolve members with that exact helper,
including temporarily missing Drive plans.

- Source images with the **same recomputed embroidery signature** are one
  design across hat colors, angles, and folders inside the same source root.
  The vision-analysis context contributes embroidered wording and the primary
  artwork/motif; hat color and generic cap construction are ignored.
- Distinct fresh embroidery signatures **must stay separate**, even if
  legacy Scout campaign membership is identical.
- A legacy Scout campaign may group plans only when it has zero or one
  distinct fresh signature. If it contains multiple fresh signatures, it is
  ambiguous; no weak/unanalyzed source can connect those competing groups.
- Tenant and root-folder boundaries are strict.
- Reviews Stage 2 intermediate `embroidery_*` images never enter the
  Reviews Stage 3/4 group or Stage 4 job history.
- UI pagination and keyword search operate **after grouping**, to avoid
  splitting a design across pages; no stored jobs or outputs are deleted.
- A temporary unanalysed source may only be joined provisionally through an
  unambiguous legacy Scout campaign; reanalysis can change its membership.

## Evidence and limits

Gemini's embroidery close-up analysis extracts `embroidery_text`,
`embroidery_identity`, and motif cues. The signature is **not a direct
pixel-embedding similarity comparison**. Identical text and primary motif in
two substantially different compositions can still be indistinguishable,
and OCR/model errors can split identical embroidery. These cases need
high-confidence visual embeddings or explicit human confirmation, not
an unconditional forced merge.

Regression tests: `test_embroidery_groups_stages.py` and
`test_source_plans.py`. On production, compare counts, group member previews,
and linked Stage 4 output/history after the deploy. Do not automatically rewrite
old campaign IDs or remap candidate references: a campaign may contain
historical references for multiple conflicting designs.
