# CAM Typography V2 — Fast ImageGen pilot, Scout-to-Prompt, History and Stage 2 handoff

Updated: 2026-10-30. Scope: Stage 1 Typography V2 only. The code in this checkout has NOT been deployed or enabled merely by authoring this runbook.

## Simplest supported flow

1. Scout or upload one legally usable typography design into the existing Typography Ideas Library. Verify source rights and approve the reference.
2. Use existing Visual Analyzer (G3) and accept a current Style DNA. No new service, vectorization engine, prompt database, worker, or Windows application.
3. Select an existing Stage 0 picked keyword. Style Lab previews three SVG drafts, and now shows a **Scout-to-Prompt Design Brief** derived from the accepted Style DNA. The brief describes only font weight, stroke contrast, proportions, text hierarchy, primary text path, alignment, safe spatial relations and overlap. It excludes source words, artwork pixels, URLs, arbitrary analyzer prose and trademarks.
4. Run **one** native ImageGen slot with the already implemented concept-short-v2 CLI. It uses the server-owned plan/prompt and exact original keyword, transparent 2048x1024 target and genuine provider provenance check. ImageGen alone does not prove spelling or embroidery quality.
5. Review the generated PNG through the existing G6 human-review operator workflow. Only explicitly accepted, spelling-verified slots can be saved. Keep partial output counts genuine (1-5, never fabricate six).
6. Use the existing `POST /typography/batches/{batch_id}/save-reviewed` endpoint to persist selected verified PNGs to managed storage and immutable V2-only Output History. Inspect `GET /typography/history/{keyword_id}`.
7. Explicitly select a saved version through `POST /typography/stage2/handoff`. No Stage 2 job or Scout starts automatically.

## Production activation preflight

Production main and backend release must contain this code and the current migrations, and the active database must have 0188/0189. Check a fresh working restore and a usable rollback before production migration or enabling the pilot; do not set `PILOT_ROLLBACK_VERIFIED=true` based only on an untested backup. At last inspection root disk was 91% used (4.4GB free), so do not create an additional full on-VPS database copy. Preserve existing current/previous releases and user media. Never enable V2 system-wide; use a single explicitly authorized tenant ID.

Required flags (only after verification):
```dotenv
TYPOGRAPHY_V2_ENABLED=true
TYPOGRAPHY_V2_CANARY_TENANT_IDS=<AUTHORIZED_TENANT_ID>
TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=true
TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=true
TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=true
TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=true
IMAGE_GENERATION_ENABLED=true
CODEX_IMAGE_GENERATION_ENABLED=true
MANAGED_ASSET_STORAGE_ENABLED=true
```

The existing Codex runtime must have the repository's `concept-short-v2` skill installed under its configured `CODEX_IMAGE_HOME/skills/`, and the tenant's current accepted rights-cleared reference/DNA and picked keyword must exist. No Cloudflare or new API key is needed by this pilot path.

## Operator one-slot run

After the above preflight, reuse the current Python venv and configure the authenticated operator/tenant/keyword/reference and a new UUID4. First issue the command without `--execute` for a zero-provider dry-run; add `--execute` only in the authorized pilot.

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.modules.realistic_review_ugc.typography_imagegen_once \
  --tenant-id TENANT --user-id OPERATOR --keyword-id PICKED_KEYWORD_UUID \
  --reference-id APPROVED_REFERENCE_UUID --batch-id NEW_UUID4 --slot 1
```

Run the existing review CLI before saving. Saved outputs use the managed provider and are idempotent per tenant/keyword/batch/slot; Stage 1 V1 rows are not overwritten. Only a manually selected saved version is handed off to Stage 2.

## Acceptance before calling this live

- Check source permission and tenant canary; DNA accepted and reference still approved.
- One actual native ImageGen call with transparent PNG provenance and no duplicate/cropped content.
- Human confirms every character, punctuation, diacritics and stitch readiness.
- Save one reviewed slot, fetch immutable V2 History, retry save with zero new writes.
- Select/unselect saved version and inspect Stage 2 source plan; zero new Stage 2 jobs.
- Confirm V1 Stage 1 and existing Stage 2 rows are unchanged; API health and rollback remain valid.
- Verify developer tests and actual frontend build against the same commit. Do not state production activation or provider smoke success without observed evidence.

## Deliberately omitted for MVP

Auto-scout orchestration, a separate prompt database/table, new message broker, second Windows tool, six-image auto-batch, retry queue, plagiarism model, image-to-image copying of the Scout artwork, automatic Stage 2 generation or new migration. The existing structured DNA and plan SHA are sufficient.
