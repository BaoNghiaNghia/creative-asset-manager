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

## Gate before any activation

1. CI must run the migration through the actual PostgreSQL 12/16 compatible chain, check correct single Alembic head and verify downgrade denial with populated data.
2. PostgreSQL concurrency testing must race two claims of one device, race a stale token against renewal/release, and exercise crashed/expired owners with unchanged ProcessingJob authority.
3. Obtain independently verified off-host backup/restore and rollback evidence for the production migration chain; the earlier 0207 release gate remains a blocker. Do not run full restore on production disk.
4. Keep all execution, learning, idle report and dispatch flags OFF unless their own separate gates are satisfied. No job routing, provider traffic, or policy promotion from this change.
5. Release from a clean exact commit on `main` using the reviewed deploy script, retain current plus rollback, verify version/live/ready and queues. Abort if health or database schema doesn't match.

**Rollback:** disable any future slot feature and roll back code only. Never downgrade a populated slot ledger; its fencing history must survive.
