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

## Scope boundary and remaining production work

This is **not yet** G6.3 publication or a Stage 1 / Stage 2 output handoff. Do not enable V2 image-generation workers based on this checkpoint alone. Remaining work includes authenticated RBAC review UI/API, durable database job and asset upload identity, transactional Stage 1 version history for 1–5 accepted PNGs, authorized per-image Stage 2 selection, ZIP/export and rollback with product-specific QA. V1 Stage 1 and Stage 2 remain unchanged. There are no provider calls or remote storage operations in this implementation.

Production database is still at Alembic `0187`. Typography migrations `0188/0189` and backend activation require a fresh complete backup and independent restore validation; user media and rollback releases must be preserved.
