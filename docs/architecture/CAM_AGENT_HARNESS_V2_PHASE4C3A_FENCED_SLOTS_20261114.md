# CAM Agent Harness V2 — Phase 4C.3A / inactive fenced slots

**Date:** 2026-11-14. **Implementation:** branch `feat/cam-agent-harness-phase4c3-fencing-20261114`.
**Status:** code-only candidate, NOT deployed, NOT authorized for dispatch.

## Contract

- Alembic 0209 adds an initially **empty** `cam_agent_executor_slots` table; one slot row per tenant + registered Image Agent, unique tenant/job, exact job FK, monotonically increasing bigint fencing token, bounded expiry, and no new worker or scheduler.
- `executor_slots.py` is **unwired** to routes and to the normal ProcessingJob claim loop. Callers explicitly pass `enabled=True` (default FALSE) and `emergency_stop=False` (default TRUE); PostgreSQL is mandatory because SQLite cannot provide required row locks.
- Reserve requires an active, correctly named, tenant-owned paired Image Agent and an already-claimed, noncancelled `etsy_design_extract` job with the matching owner, sane attempt/claim timestamps and unexpired source ProcessingJob lease. It refuses any other unresolved processing job owned by that device.
- First insert is protected by `INSERT ... ON CONFLICT DO NOTHING`, then PostgreSQL row lock on the unique device slot. Lease expiry is capped at 120 s and cannot outlive the existing job lease. No new ProcessingJob is claimed.
- Re-reserving a live identical job returns its existing token; a stale token cannot renew/release after a later reservation. Expired slots require reconciliation if the old job is still processing; release requires the old job to be no longer processing. All changes must be committed in the same database transaction as the authoritative queue operation.
- Slot/HMAC/idle receipts **do not** independently establish browser readiness or provider success. Any future external writer must echo/check the current fence atomically at its output commit boundary. Until then, this is merely a candidate low-level primitive, not exactly-once execution.

## Phase 4C.3B: claim-epoch fencing (2026-11-14)

- A job ID is **not** an execution identity: the existing Image Agent claim endpoint increments `attempt_count` and replaces `claimed_at` on retry. The original slot implementation could reuse the old fence after a same-job reclaim. The ledger now persists both `claim_attempt_count` and `claim_started_at`, and all reserve/renew/release/output-validation calls compare the exact current claim epoch.
- An identical, unexpired claim remains idempotent (same token); an expired slot with the *same* claim cannot self-renew and must be reconciled. A **newer** claim for the same job receives a strictly incremented token only once the previous slot lease has expired and both attempt count and claim timestamp advance. Early replacement fails closed. Other unresolved jobs on that device also block admission.
- `verify_image_output_fence` provides an **inert**, transaction-scoped row-lock check of current job/attempt, device registration, global stop, token and both leases. No HTTP result route calls it yet. The future output writer must supply the current token, hold job and slot locks through its database commit, and coordinate any filesystem/provider side effect with an idempotent immutable receipt. Merely calling this function does not make external writes atomic or prove provider/browser readiness.
- The existing GitHub CI PostgreSQL 12.22/16.4 matrix is extended with same-claim concurrency and claim-reclaim/stale-write tests; no VPS PostgreSQL test cluster is introduced. The 0209 schema change is only safe **before** migration is ever deployed; if 0209 had been installed, use a separate additive migration instead of editing history.

## Phase 4C.3C — CI recovery and conservative handoff (2026-11-14)

- Hosted PostgreSQL 12.22/16.4 CI run #3011 exposed a reproducible **test fixture error**: the test reassigned `claimed_at` to +35 seconds, but then asked admission at +10 seconds, more than the hard +5 second future-skew allowance. The correct `job_claim_not_verified` result was incorrectly asserted to be `slot_busy`. The corrected scenario uses a forced new claim at +20 seconds (while the old slot is live) and then waits until the old slot expires at +30 seconds before reserving the newer attempt at +31 seconds. Both old-token rejection and the new fence are rechecked at +31 seconds. This does **not** relax production skew checks.
- Cross-device handoffs now read-lock any pre-existing slot referencing the job before inserting the new device slot, returning `job_reserved_elsewhere` instead of raising a unique constraint exception. An expired slot still requires independent, audited reconciliation; no automatic migration of a job between device slots.
- Idempotent same-attempt reserve no longer returns an active token if that slot has outlived a shortened authoritative ProcessingJob lease. A separate renewal may shorten it before the output fence can pass.
- Added real PostgreSQL negative tests for cross-device re-claim, incorrect epoch monotonicity, and source-lease shortening. The slot code remains unwired/default-OFF and the 0209 table remains undeployed. No external result endpoint, browser, worker, paid model, Windows agent or queue processing is changed.
- The branch was reconciled with current `main` after PRs #167–169 so review/CI includes the latest frontend without introducing a concurrent frontend change.

## Gate before any activation

1. CI must run the migration through the actual PostgreSQL 12/16 compatible chain, check correct single Alembic head and verify downgrade denial with populated data.
2. PostgreSQL concurrency testing must race two claims of one device, race a stale token against renewal/release, and exercise crashed/expired owners with unchanged ProcessingJob authority.
3. Obtain independently verified off-host backup/restore and rollback evidence for the production migration chain; the earlier 0207 release gate remains a blocker. Do not run full restore on production disk.
4. Keep all execution, learning, idle report and dispatch flags OFF unless their own separate gates are satisfied. No job routing, provider traffic, or policy promotion from this change.
5. Release from a clean exact commit on `main` using the reviewed deploy script, retain current plus rollback, verify version/live/ready and queues. Abort if health or database schema doesn't match.

**Rollback:** disable any future slot feature and roll back code only. Never downgrade a populated slot ledger; its fencing history must survive.
