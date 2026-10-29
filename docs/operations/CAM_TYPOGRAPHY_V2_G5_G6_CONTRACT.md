# CAM Typography Engine V2 — G5 QA + G6 Plan-Only Handoff

Status: **code pilot, OFF by default**, 2026-10-29. This is not a production rollout, image-generation run, or approval of final embroidery images. Stage 1 V1 (`concept-short-v1`) and Stage 2 output processing are unchanged.

## What is now implemented

The existing canary-scoped `POST /api/v1/realistic-review-ugc/typography/previews` keeps returning the three G4 SVG drafts; it also returns:

- `quality`: versioned `g5-qa/v1` source-vector QA, exact source keyword check (including Unicode, punctuation and spaces), approved SVG-tag/font safety, duplicate layout/vector detection, and **explicit manual-review flags**.
- `generation_plan`: `concept-short-v2/plan-v1`, six distinct deterministic **pending** slot recipes, each containing the original literal keyword, concept layout, OFL-font mood, reviewed Style DNA path/align guidance, embroidery realism guidance and a transparent 2048 × 1024 PNG target. Plan is **not runnable** (`ready_for_imagegen=false`), creates **zero jobs**, stores nothing and invokes **zero provider calls**.
- The prepared `typography-v2/slots-v1` contract explicitly contains `requested_slots=6`, `eligible_slots`, `failed_slots`, `pending_slots`, `needs_review_slots`, `actual_count`, `partial` and `completed`. No imaginary output fills missing slots.

The standalone PNG QA `inspect_typography_png` / `audit_png_slot_set` checks genuine supplied bytes in memory: PNG, exact 2048 × 1024, real RGBA with transparent alpha margin, nonempty bounded artwork, SHA-256 distinctness, and advisory near-duplicate dHash. **Pixel inspection cannot prove AI spelling, embroidery feasibility in mm or originality**; those remain human QA. None of the new PNG helpers is wired to the V1 worker.

## G5 & G6 acceptance policy

1. Vector `technical_pass` means source SVG text equals the Stage 0 keyword byte-for-byte at the Unicode string level. It **does not** establish that an ImageGen raster spells the word correctly.
2. A candidate PNG that fails image/alpha/canvas checks, or reuses an already accepted SHA-256, must be rejected. Low perceptual distance is an originality **warning**, not a plagiarism verdict.
3. A generated PNG becomes `eligible` only after **both** G5 technical checks and a separate explicit human acceptance with `image_spelling_verified=true`; model self-reports and original JSON text are insufficient.
4. If some slots pass and the rest fail, `partial=true` and `actual_count<6`. Pending or review-required slots block publishing. V1 job semantics are untouched.
5. The Stage 1 image history/export and every downstream Stage 2 consumer must be tested with 1–5 genuine outputs **before enabling** a V2 image job on production. Do not mark a job `completed` to compensate for missing or failed edits.

## Remaining steps to actually generate images

- G6.1 **code merged via PR #65, runtime OFF**: separate `concept-short-v2` native ImageGen single-slot pilot consumes the versioned plan and verifies native PNG hash/G5 QA. Invocation is operator-only, one slot per run with global single-flight, no auto retry or V1 fallback. It has not completed a real provider smoke test. See `docs/operations/CAM_TYPOGRAPHY_V2_G6_NATIVE_PILOT.md` for implementation and flags.
- G6.2: add separate canary/permission-gated durable job and one-slot checkpoints, technical PNG QA before upload, protected original files, explicit human review, and auditable output manifest. No V1 runner overwrite or Cloudflare bypass without provider policy validation.
- G6.3: adapt Stage 1 list/history/export and Stage 2 handoff to genuine `partial` outputs and test rollback.
- G0.1 still blocks runtime migration/activation: verify a current full-data backup, off-host restore of approximately 4 GB, backward compatibility of `0188/0189`, independent rollback and VPS capacity. Existing readonly `cam_backup` setup is only a prerequisite.
- G9: run with eight rights-verified references and twenty keywords, check source lettering visually and cost/latency; Windows Scout is not required to acquire manually uploaded pilot references.

## Testing

Use the isolated checkout's API code with the existing Python test environment; the commands do not access production data or call an image provider:

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest -q \
  apps/api/tests/modules/realistic_review_ugc/test_typography_quality_gate.py \
  apps/api/tests/modules/realistic_review_ugc/test_typography_generation_plan.py \
  apps/api/tests/modules/realistic_review_ugc/test_typography_v2_api.py \
  apps/api/tests/modules/realistic_review_ugc/test_typography_preview.py
```

Maintain `TYPOGRAPHY_V2_ENABLED=false`, empty canary allowlist and both pilot worker/rollback flags OFF until G0 permits rollout.
