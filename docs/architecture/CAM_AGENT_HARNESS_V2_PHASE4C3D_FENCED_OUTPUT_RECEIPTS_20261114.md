# CAM Agent Harness V2 — Phase 4C.3D: Inert fenced PNG output receipts

**Date:** 2026-11-14. **Status:** code-only candidate, no live route/worker, no production migration.

## Problem and failure model

The existing Etsy Image Agent `POST /image-agents/{agent_id}/result` writes
`{job_id}.png` to local storage before the PostgreSQL job COMMIT. This path
cannot guarantee replay isolation across multiple claim epochs of the same
`job_id`, even if a durable device slot has a fencing token. A job lease is
not an output acceptance guarantee. No new code in this phase changes the
legacy endpoint or its existing task queue.

## Design and exact order

1. A previously claimed `etsy_design_extract` ProcessingJob first reserves its
   PostgreSQL 0209 device slot (separate Phase 4C.3C). Slot verification locks
   **ProcessingJob -> active paired Image Agent -> device slot**. Feature flags
   default OFF; emergency stop defaults ON; non-PostgreSQL calls fail closed.
2. `prepare_fenced_image_receipt` validates the output's SHA-256, size,
   trustworthy source SHA-256 and approved engine, then re-verifies the current
   claim epoch, slot token, device and leases. Under the source/slot lock it
   creates exactly one `prepared` receipt **per tenant/job across all retries**,
   bound to agent, attempt count, claimed_at, token, PNG digest, size, source
   digest and engine. Caller commits the prepared row before staging bytes.
   An identical replay returns the same receipt. Different bytes, source,
   engine or epoch cannot silently replace the receipt.
3. After the caller has independently validated source, browser/provider
   provenance and permissions, `accept_fenced_image_receipt` (explicit
   `provenance_verified=True`) re-locks the current claim and receipt,
   re-verifies fencing and checks PNG format, pixel stream, digest and size.
   It writes a content-addressed blob under `fenced-v1/<prefix>/<receipt>.png`
   inside a caller-provisioned absolute local storage root. A restricted
   exclusive temp file is `fsync`'d and hard-linked to the final filename
   **without overwrite**; existing names must pass the full hash/size check.
   The containing directory is also `fsync`'d.
4. **Only after** file persistence succeeds, the same Session transaction
   updates both receipt to `accepted` and ProcessingJob to `completed`.
   The result is explicitly `needs_review`, `manual_qa_required=true`,
   `print_ready=false`, `rights_status=unverified`. Caller commits.
   Old/cancelled/reclaimed claims cannot accept; the active slot is released
   through the separately reviewed reconciliation procedure.
5. If the process crashes after filesystem fsync but before SQL commit, the
   blob may be orphaned, but never becomes an accepted/public result.
   A matching still-valid claim can verify/reuse the exact content on retry.
   A prepared stale epoch or incompatible artifact **requires operator
   reconciliation**; it is never automatically erased or claimed by another
   agent. If an accepted blob disappears, exact replay refuses to affirm it.
   No filesystem/DB distributed transaction or global exactly-once provider
   behavior is claimed.

## Database/release boundary

- Alembic `0210_cam_agent_image_output_receipts` depends on inert 0209, adds
  an **empty** table with tenant/job PK, unique receipt, source and device FKs,
  positive epochs, 22 MB bound, lifecycle CHECK and partial query index.
  No backfill, queue conversion or automatic dispatcher.
- Populated 0210 cannot be downgraded. Code-only rollback must preserve both
  0209 fences and 0210 receipts. The existing 0207 off-host restore and
  production rollback gate **is still BLOCKED**; do not run 0209/0210 in
  production or weaken the gate merely because disposable CI migrations pass.
- No active caller imports `fenced_output.py`; no feature toggles, Windows
  agent update, image model calls, browser navigation or extra VPS environments.
- Browser execution proof, actual multi-placement source lineage, API-authenticated
  result proof, dedicated signer, recovery UX, storage lifecycle/reconciliation,
  and a reviewed cutover from the legacy PNG path remain separate gates.

## Verification

Disposable CI PostgreSQL 12.22/16.4: prepared/idempotent receipts,
concurrent prepares, conflict/stale epochs, source mismatch, fail-closed
disabled/unsupported cases, PNG corruption, missing storage, atomic acceptance,
replay after success, simulated crash **after fsync/before DB commit**, and
no overwrite. Single Alembic head and empty additive migration checked.
Local syntax/diff checks are not substitutes for hosted PostgreSQL CI.

**Do not deploy** until CI is green and the independently verified off-host
production-backup restore/rollback evidence is available.
