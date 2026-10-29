# CAM Typography Engine V2 — G6.1 Native ImageGen Single-Slot Pilot

Date: 2026-10-29. **Source implementation only; production flags OFF.** This is a concrete native ImageGen execution path, not yet a Stage 1 job, public UI operation, completed six-output Skill or production rollout.

## Implementation

- `apps/api/managed_skills/concept-short-v2/` contains an isolated `SKILL.md` and `manifest.json` with **only** the `typography_v2_single_slot` workflow. Stage 1 V1 skill selection will not offer it; `concept-short-v1` and Stage 2 remain unchanged.
- `CodexImageGenRunner(output_contract="typography_v2_single_slot")` uses the real `$imagegen` tool through existing authenticated, job-isolated Codex runtime. Accepts only `output/final.png` whose SHA-256 exactly matches a native PNG in **this attempt's private `CODEX_HOME/generated_images` cache**. Text replies, fake PNGs, event counters and files from another job cannot establish provenance. No API-key or Cloudflare fallback.
- `typography_imagegen_pilot.py` first resolves the tenant-picked Stage 0 keyword and **current human-accepted** DNA of a visible, approved rights-cleared reference. The server-owned G6 plan selects **one slot (1–6)**. The only model call executes that slot's prompt. Afterward, it **re-resolves the reference, rights, keyword and DNA from DB** before writing.
- Genuine transparent native PNGs can be centered on 2048×1024 with transparent padding **without scaling or inventing pixels**, retaining the native hash separately from the final PNG hash. No opaque fake backgrounds, resampled text, clipped oversized artwork, synthetic stitch filters or zero-output success. G5 checks the final image. Quality warnings still require manual spelling, originality and embroidery review.
- Valid outputs are staged under `IMAGE_GENERATION_STAGING_ROOT/typography-v2-pilot/<tenant-hash>/<batch-uuid4>/design_XX.png`, with immutable `slot_XX.json`: `review=pending`, `image_spelling_verified=false`, `eligible_for_publication=false`, `stage1_output_saved=false`. A single global worker lock serializes all V2 pilot calls (even across tenants and batch IDs); no-overwrite file creation rejects duplicate runs and duplicate SHA across slots. Per-job model caches and previews are cleaned. **No Stage 1 Output History, ProcessingJob, Stage 2, public route or scheduled worker is modified.**

## Safety gates and operator command

All of these must be independently enabled by production change control **only after G0.1 backup, off-host restore, migration compatibility and rollback verification**:

```
TYPOGRAPHY_V2_ENABLED=true
TYPOGRAPHY_V2_CANARY_TENANT_IDS=<authorized-tenant-id>
TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=true
TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=true
IMAGE_GENERATION_ENABLED=true
CODEX_IMAGE_GENERATION_ENABLED=true
```

`TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED` is **new** and defaults `false`, independent of G3's analyzer worker flag. The V2 skill must be installed/validated under the designated Codex worker's `CODEX_IMAGE_HOME/skills/concept-short-v2`; merely merging repository files does not install it.

Dry-run without `--execute` does not load production Settings/DB/storage or call the model:

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m \
  app.modules.realistic_review_ugc.typography_imagegen_once \
  --tenant-id TENANT --user-id OWNER --keyword-id KEYWORD_UUID \
  --reference-id REFERENCE_UUID \
  --batch-id 01234567-89ab-4def-8123-456789abcdef --slot 1
```

Append `--execute` **only** in the approved canary. Same batch ID groups up to six independently reviewed slots. Repeating a stored slot fails closed without another provider call. A new batch ID means an intentional separate pilot, not an automatic retry. The command prints bounded status, never a literal keyword, prompt, credential, native image bytes or reference URL.

## Next gates

1. **G0.1 blocker still applies:** full 4GB backup, off-host restore, verified migrations 0188/0189 rollback, capacity headroom. At last inspection root was ~89% and swap ~2/2 GiB.
2. **G6.2:** add a durable, authorized human image-review decision and auditing before any upload. Review MUST inspect actual PNG lettering, not just source SVG. No automatic promotion.
3. **G6.3:** wire approved real outputs to durable Stage 1 Output History and export, preserving original numbered slots and supporting partial 1–5 across Stage 2; prove tenant, rollback and idempotency.
4. **G9:** genuine paid/native provider smoke run with 8 rights-verified references and 20 keywords, evidence of quality, latency and cost. Tests with mocked provider do NOT establish real model output.

Code test: `PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/realistic_review_ugc/test_typography_imagegen_pilot.py apps/api/tests/modules/realistic_review_ugc/test_typography_v2_api.py apps/api/tests/modules/realistic_review_ugc/test_concept_short_v1_runtime.py`.
