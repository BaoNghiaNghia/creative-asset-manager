# CAM Typography V2 G6.3 — Private reviewed partial-batch checkpoint

Status: **source-only, OFF by default** (2026-10-29). This module does not activate V2 on production.

## Purpose

Aggregate up to six independently generated, technically verified and explicitly human-reviewed ImageGen PNG slots. One to five accepted outputs must remain one to five genuine outputs; do not synthesize missing designs or falsely mark the six-slot batch complete.

Implemented:

- `typography_reviewed_batch.py`: read-only tenant-isolated batch inspection under the G6 ImageGen single-flight lock. Confirms the original plan SHA, each image's G6.1 manifest SHA and G5 PNG QA, and any G6.2 immutable human decision. Revalidates current rights-approved reference, Style DNA, keyword and tenant canary before and after reading.
- Reports accepted slot numbers in their original order, byte sizes and private local paths, together with the G5 six-slot contract's `failed_slots`, `pending_slots`, `needs_review_slots`, `actual_count`, `partial` and `completed`. Missing checkpoints are pending; corrupted, incomplete, symlinked, modified or mismatched records fail closed.
- Explicitly marks every private checkpoint `published=false`, `stage1_output_saved=false`, `stage2_handoff_performed=false` and `eligible_for_publication=false` regardless of whether the G5 quality contract counts six approved images.
- `typography_reviewed_batch_once.py`: operator-only, dry-run-by-default CLI. Its `--execute` mode performs a **read-only** inspection using authenticated caller-controlled CLI access and current DB plan resolution; output excludes private paths, prompts and original keyword. CLI user ID must represent an independently authorized operator. It does not provide an HTTP endpoint.

Example dry-run (never needs a configured database or provider):

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.modules.realistic_review_ugc.typography_reviewed_batch_once \
  --tenant-id TENANT --user-id OPERATOR --keyword-id KEYWORD_UUID \
  --reference-id REFERENCE_UUID --batch-id CANONICAL_UUID4
```

## G6.3 private selection preview — 30 October 2026

The reviewed-batch inspector now optionally accepts `keyword_id` and
`selected_slots` (one through six unique integers). Within the original
single-flight lock, and after current source-rights/Style DNA revalidation,
it rejects unreviewed, rejected, duplicate or out-of-range selections. Accepted
selections are sorted and bound to the tenant, Stage 0 keyword ID, batch ID,
plan checksum and each verified PNG SHA-256/byte count. An identical selection
has a deterministic `selection_sha256` idempotency hint; a different keyword
or batch has a different fingerprint.

`selection_preview` contains no private PNG paths, raw keyword, access token,
or externally accessible file IDs. It explicitly returns
`published=false`, `stage1_output_saved=false`,
`stage2_handoff_performed=false`, `can_queue_stage2=false` and
`requires_managed_storage_commit=true`. The preview is never an authorization
to publish: it cannot write output history or create a Stage 2 source plan.
A future writer must separately verify the caller's RBAC, source rights,
reviewed bytes, managed storage upload receipt, transactional job ownership,
and idempotent DB publication. No migration or new dependency is introduced.

Focused regression tests cover 1–6 real accepted slots, subset ordering,
duplicate/pending/rejected/symlink/stale-source rejection and stable binding.
Production pilot, worker and Scout flags remain unchanged. This is a
source-only handoff precursor, not a completed durable G6.3 release.

## G6.3 isolated durable V2 output-history writer — 30 October 2026

Source-only, **not deployed**. `typography_output_history.py` adds an explicitly
operator-authorized and separate-flag-gated writer for a selected set of
human-approved ImageGen PNGs. It stores each verified PNG through the managed
storage adapter under a deterministic tenant/keyword/batch/slot asset ID, then
locks the picked Stage 0 keyword, rechecks reference rights and effective Style
DNA, and appends an immutable `rrugc_image_output_versions` row with
`stage="typography_v2"`. The original `stage1` job rows and pointer are
never updated. Existing stage1 history and Stage 2 plans remain untouched.

The writer does not automatically run from UI, a queue or a worker. Its new
`TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED` flag defaults to **false**, and all
existing ImageGen pilot/rollback/tenant gates must also pass. Partial
checkpointing is intentional: a failed later upload never invalidates earlier
saved rows; repeated calls reuse existing slot records only when reviewed
SHA, plan and byte count match. No deletion occurs on upload/database failure.

`list_v2_history()` supplies only read-only private metadata, requires
separately enforced caller RBAC, and never returns managed Drive IDs.
**This does not yet create Stage 2 source plans or expose a public route.**
Before activation, complete full restore/rollback evidence, integration tests
with an actual managed storage adapter and PostgreSQL, and authorized API QA.
No migration, new dependency or production secret is required for this
source-only implementation. Never use a selection preview as publication
authorization.

## Scope boundary and remaining production work

This is **not yet** G6.3 publication or a Stage 1 / Stage 2 output handoff. Do not enable V2 image-generation workers based on this checkpoint alone. Remaining work includes authenticated RBAC review UI/API, durable database job and asset upload identity, transactional Stage 1 version history for 1–5 accepted PNGs, authorized per-image Stage 2 selection, ZIP/export and rollback with product-specific QA. V1 Stage 1 and Stage 2 remain unchanged. There are no provider calls or remote storage operations in this implementation.

Production database is still at Alembic `0187`. Typography migrations `0188/0189` and backend activation require a fresh complete backup and independent restore validation; user media and rollback releases must be preserved.
