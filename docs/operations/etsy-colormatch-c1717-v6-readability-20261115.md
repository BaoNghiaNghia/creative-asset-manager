# CAM Etsy Spy — C1717 ColorMatch V6 legibility rollout

## Goal
Exclude low-legibility garment backgrounds *before* market popularity ranking.
Example regressions: black lettering in colorful flamingo artwork on True Navy
and a text-dense veterinary alphabet on Black. Original user screenshots are
visual references; synthetic transparent regression PNGs are used in tests.

## Recommendation rules
- The current C1717 SMS catalog has 72 named colors; only the 54 configured
  colors with verified SMS HEX can be automatically recommended.
- V6 computes bounded, deterministic alpha-aware contrast scores for the
  192px preview, plus a conservative text-likeness proxy from transparent ink
  connected components, significant dark/light neutral ink and aligned glyphs.
  This is **not OCR** and cannot guarantee actual printed readability.
- A palette candidate failing the ink legibility guard enters `unsafe_colors`
  and **cannot** be rescued by SMS sales speed, baseline average or the 7-color
  target. Popularity only ranks colors already accepted by the guard.
- Usually recommend 7–10 diverse safe colors. If only 0–6 pass, persist the
  smaller honest set with `insufficient_safe_colors: true`; do not invent
  illegible colors.
- UI shows a warning for manual/previously saved unsafe backgrounds and flags
  risky swatches in the expanded palette. Transparent preview never edits PNG.
- Metadata version `colormatch-sms-c1717-v6`; method
  `thumbnail_ink_readability_sales_prior`. All advisory metadata is bound
  to the immutable artwork SHA. V1–V5 remain readable during rollout.

## Regression tests
- V6 flamingo fixture: Black, True Navy, Pepper rejected; White remains safe.
- V6 veterinary fixture: those dark colors rejected even when animal colors
  visually dominate. Dense/mixed ink may have fewer than 7 safe options.
- No-safe-color results survive backend projection, and untrusted `unsafe_colors`
  are sanitized.
- React checks manual override warning and per-design behavior.

## Production deployment
Only release the verified, clean, fast-forward `main` commit. Run existing
scripts from a clean release checkout, with current+rollback retention and
`--no-migrate` when schema is compatible:
```bash
bash scripts/cam-rebuild-backend.sh --commit COMMIT --no-migrate --keep-releases 2
CAM_FRONTEND_RELEASE_KEEP=2 bash scripts/deploy-cam-frontend.sh --commit COMMIT
```
Confirm `/version`, `/live`, `/ready`, Nginx and managed workers. Never
deploy directly from the dirty top-level server checkout.

## Full Picked backfill
Default is a read-only dry-run; `--apply` updates only `color_match` after
completed-job/Picked membership/Drive thumbnail SHA and original digest checks.
There is no 10-item limit, no PNG rewrite, no new AI generation, and no
concurrent second backfill. Recommended scope = every eligible Picked design.

```bash
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100 --delay-seconds 0.5 --apply
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100
```

Verify all expected targets are `already_current`, `targets_drive` and
`targets_local` are zero on the post-run dry-run, and assess any
`unavailable_or_mismatch`, `skipped` and `errors` before claiming success.
If the process exits abnormally, rerun the idempotent command from the same
verified release after diagnosing the cause.
