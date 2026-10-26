# Concept Short v1: reference-edit Stage 1 rollout (2026-10-26)

## Scope and invariants

- Stage 1 produces six independent 1536x1024 PNG masters for an exact quote.
- Installed `concept-short-v1` is the source of truth for font, layout, wording, C4/C7 and C5/C8 substitutions, prompt and C6 wrapping.
- Only the existing `make_concepts.py` emits the six source plans. Flat code-generated reference artwork is NOT a finished embroidered output.
- When `CONCEPT_SHORT_CLOUDFLARE_EDIT_ENABLED=true`, the image worker submits each corresponding original `A_input.png` with its `A_prompt.txt` to Cloudflare Workers AI `@cf/black-forest-labs/flux-2-klein-4b`, one paid reference edit per required slot.
- Cloudflare requires image input smaller than 512x512. The adapter downsamples the *reference* to 504 pixels maximum side; outputs remain 1536x1024. This introduces an inherent fidelity risk on small typography. Avoid claiming exact text matching without visually inspecting the actual outputs.
- The adapter never falls back to synthetic stitch, text-only rendering, or Codex CLI if the Cloudflare API is unavailable or credentials are missing.
- Pre-existing completed jobs and Drive output histories are not requeued. Only previously failed Stage 1 jobs may be explicitly regenerated. Partial new-run PNG checkpoints can be retried individually without regenerating already validated files.

## Preconditions and permissions

- Production config (root-owned, secret values remain outside Git): `CLOUDFLARE_AI_ACCOUNT_ID`, `CLOUDFLARE_AI_API_TOKEN` and `CONCEPT_SHORT_CLOUDFLARE_EDIT_ENABLED=true`.
- Existing production Stage 1 processing and managed storage gates remain required. No database migration or new Python package is needed.
- A paid single-image canary was verified against real Cloudflare API before rollout: HTTP 200, `result.image`, 1536x1024 PNG; the actual image was inspected for shape and stitching.
- Use the reviewed `scripts/cam-rebuild-backend.sh --commit <SHA>` release route, never copy edited source into `current`. Confirm the requested SHA is on `origin/main`, current/previous links are protected, and the image workers are healthy.
- Rollout is successful only when an explicitly selected Stage 1 job produces six distinct final PNGs, receives six successful API edits, and Managed Storage records six output versions. Fail with a clear provider code otherwise.

## Failure and recovery

- `concept_short_flux_rate_limited`: defer using the Stage 1 retry budget, preserving prior PNG checkpoints.
- `concept_short_flux_unavailable`: resume only when 1–5 valid PNG checkpoints exist. Never regenerate previously completed final files automatically.
- `concept_short_flux_not_configured` and authentication errors: fail closed. Do not silently reroute to Codex or a code-based faux stitch renderer.
- Past `concept_short_slots_ineligible` jobs still display their historical failure until explicitly regenerated with the currently installed patched C6 skill.
- Past `concept_short_image_edits_missing` jobs also remain failed until requeued. Do not rewrite their historical error records to pretend generation succeeded.

## Rollback

- Turn the dedicated feature flag off to prevent further Cloudflare edits; this restores the legacy Codex adapter for future runs (known to fail when image-edit is absent). To avoid creating false stitched outputs, temporarily avoid running `concept-short-v1` until a verified provider is restored.
- Use `scripts/cam-rebuild-backend.sh --rollback` to atomically restore the previous backend release. No schema downgrade is required.
- Production configuration/credentials, PostgreSQL job history and managed output files are never deleted as part of rollback.
