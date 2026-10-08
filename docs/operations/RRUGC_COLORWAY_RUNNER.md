# RRUGC Stage 2 — 13 official embroidery colorways

Implementation status (2026-10-08): code and migration are implemented in the
checkout; 41 backend unit/integration-stub tests, 542 frontend tests, typecheck, and
Stage 2 feature QA passed. An authenticated real-provider end-to-end run is still
required before treating this as production-ready. This does **not** replace any
of the six user-facing stages or link Stage 1 keyword outputs to Stage 2.

## Skill selection
- Stock photo authority remains `gatorhats-8869-image-studio/assets/stock` (13 physically present Valucap 8869 colors).
- Generation now prefers a **separate** bundled `gatorhats-8869-scale-image` Skill, because Image Studio's own SKILL.md routes color-scaling-only requests to a separate workflow. Each job edits the official stock front image with exactly one `design_reference` image. Existing user-selected Skills remain selectable.
- Both project-managed skills are synced by the normal backend release script. The Scale Image Skill is not a new dependency or provider credential.

## Contract
- Stage 2 accepts only the current tenant's available `embroidery_*` source plans.
- Stock base images are the 13 physical `front.jpg` files named in
  `deploy/codex/skills/gatorhats-8869-image-studio/references/STOCK_MANIFEST.md`.
  They must be installed under `CODEX_IMAGE_HOME/skills/gatorhats-8869-image-studio/assets/stock`.
- A request queues up to 50 source designs, creating one durable processing job
  for each missing (source plan, source revision, official color).
- A database uniqueness constraint enforces exactly one logical job per revision
  and color. Repeating an HTTP queue request never overwrites completed jobs.
- The queue captures the selected Skill/version/bundle SHA and stock photo SHA.
  The worker rejects changed source, stock or Skill rather than silently
  generating from a different revision.
- Automatic processing retry uses the existing worker policy, maximum 3 attempts.
  Manual retry only permits failed jobs, maximum 3 retries; completed outputs
  are not changed by retries. New source revisions create new jobs, preserving history.
- Output is saved alongside the source in managed storage using an `output_`
  prefix and is excluded from RRUGC source discovery by existing filters.
- `GET /api/v1/realistic-review-ugc/colorways` and private output reads are
  tenant-scoped. Mutations require the existing RRUGC RUN permission, listing
  and output reads require READ.

## Pre-release checks
1. Verify all 13 installed stock images match the manifest in the deployed
   Codex home (no public HTTP downloads used).
2. Run: `python -m pytest apps/api/tests/modules/realistic_review_ugc/test_colorways.py -q`
   in a Python environment with project requirements.
3. Run frontend TypeScript, RRUGC Vitest, UI feature-state QA, and production
   authenticated end-to-end on a single non-critical design.
4. Verify job lease recovery, retry on provider 429/timeout, 13 unique outputs,
   failed-color-only retry, old revision preservation, and output CDN permissions.
5. Only then migrate/deploy with an explicitly authorized production release.
   Check capacity before queueing large design batches.

## Migration / security / rollback
- Forward migration: `0143_rrugc_colorway_jobs` (additive model/table/index).
- No new application secrets or third-party dependencies.
- User-owned source files stay untouched. Existing output objects remain in
  managed storage after application/database rollback; never delete them
  automatically while rolling back this feature.
- Roll back by stopping new colorway queue operations, draining processing
  jobs of type `rrugc_colorway_generate`, reverting backend/client code,
  and (if safe to discard the new job history) downgrading migration `0143`.
- For production, do not drop the table while any active colorway jobs exist.
- Skill output is AI-generated: correct embroidery/color is a prompt/visual
  objective, not a cryptographic or vision-validated guarantee. Review image
  quality before use in inventory/catalog.
