# CAM PostgreSQL optimization — execution runbook v1.0

Date: 2026-10-25 (Asia/Ho_Chi_Minh). Source branch: `main`. Database: `creative_asset_manager`, PostgreSQL 16.15. Operator objective: lower claim CPU/buffers/lock contention and recover capacity **without** breaking fairness, policy ordering, `FOR UPDATE SKIP LOCKED`, idempotency, completed jobs, Inventory, or recovered assets.

## P0 — Baseline (DONE; read-only, production)
- DB 3,848 MB; 195 user tables; 704 indexes.
- `processing_jobs`: 1,860 MB total, 1,310 MB indexes, ~875,756 live estimates, ~63,940 dead estimates; count snapshot: 738,929 completed, 126,439 pending, 10,784 failed, 1 processing, 1 retry.
- `asset_ai_analyses`: 575 MB, ~430 MB TOAST. `application_logs`: 264 MB, 224,185 rows, **zero expired** by `expires_at` at measurement. `source_assets`: 275 MB. `search_operation_items`: **65,150 actual rows** even though `pg_stat_user_tables.n_live_tup=0`.
- `pg_stat_database.temp_bytes` 279 GB and 40,924 temp files are **cumulative**, not current disk occupation. Compare deltas/time before conclusions.
- 440 zero-scan indexes (~183 MB) are not presumed disposable: constraints and sparse usage require review.
- Autovacuum on, global vacuum scale factor 0.2, max workers 3, track_io_timing off, shared_preload_libraries empty. Vacuum for `processing_jobs` last observed 2026-10-24 16:14+07. No automatic vacuum recorded for `asset_ai_analyses`, but that alone does not prove a defect.
- `processing_jobs` indexes: `pipeline_latest` 464 MB, `tenant_key` 219 MB, `policy_claim` 166 MB, `available` 115 MB. Each has nonzero scan counts.
- Initial sampling found claim/policy contention; later snapshot 0 locks and 0 idle-in-transaction. Gather a 5-minute series.
- Backup timer `creative-asset-manager-db-backup.timer` is scheduled; **verified remote backup receipt + isolated restore have not been checked**.
- Read-only snapshots were obtained using `runuser -u postgres -- psql`; no schema changes or deletes occurred.

## Hard gates
1. **G0 baseline**: 3–5 representative samples, record CPU/lock waits/temp_bytes/plan/buffer and source SHA; no secrets/payloads in logs.
2. **G1 recovery**: fresh backup, remote checksum receipt, isolated database restore PASS, known RPO/RTO; timer presence alone is insufficient.
3. **G2 capacity**: >=8 GiB filesystem f_bavail free before bulk changes; WAL/index rebuild headroom adequate. On failure: read-only only.
4. **G3 semantics**: A/B proves same selected job and priority/fairness/policy/tenant filtering; concurrent two-session SKIP LOCKED correctness.
5. **G4 performance**: record query p50/p95, shared hit/read buffers, temp spill, CPU, lock waits, rejection conditions and claim fallback frequency. Reject if regression.
6. **G5 canary**: staged activation, 15–30 min monitoring (queues, completed re-run, data integrity, /live, /ready); immediate rollback on regression.
7. **G6 retention**: per-tenant dry-run, G1/G2 success, FK and idempotency proof, no AI re-run or Drive re-upload; incident tables protected until reconciliation.

## Phased work and checkpoints
| Phase | Work | State |
|---|---|---|
| P0 | Read-only database/table/index/lock/vacuum inventory | DONE baseline and 5-sample claim observation; repeat before A/B |
| P1 | Check backup receipt, isolated restore, disk/WAL | BLOCKED pending evidence |
| P2 | Autovacuum hygiene: derive per-table dead tuple threshold; consider narrow reloptions; VACUUM (ANALYZE) only when safe | BLOCKED by G1/G2 |
| P3 | Diagnose real SQLAlchemy claim query, rejection groups, blocked group backoff and eligible tenant/job_type pairs | READ-ONLY plan captured for image, visual, video-heavy; exact runtime A/B pending G1 |
| P4 | A/B with EXPLAIN (ANALYZE, BUFFERS) on safe replica/rollback transaction, two-session concurrency, same selected job | PENDING |
| P5 | Consider concurrent index maintenance only if bloat measured, no duplicate/necessary indexes removed; never VACUUM FULL | BLOCKED |
| P6 | Retention service dry-run and archived incident reconciliation; completed-job payload redaction only through official gates | BLOCKED |
| P7 | Review partitioning and archival with tenant key, unique constraints, FK/idempotency compatibility | DESIGN ONLY |

### Critical preservation rules
- Do not delete completed job rows or identity keys, DROP/TRUNCATE old tables or drop a zero-scan index by scan count alone.
- Do not re-run 55k completed images, rebuild AI, re-upload existing storage, or sign Inventory close.
- `cam_incident_20261024_job_hold` (54 MB) and `cam_incident_20261024_source_reuse` (41 MB) are restoration evidence until proven unnecessary.
- Existing `apps/api/app/modules/retention/service.py` supports content redaction while preserving job rows; production retention guard requires valid backup receipt, tested restore and disk gate. Refer to `docs/operations/DISK_PRESSURE_RETENTION_20261022.md`, `DATABASE_BACKUP.md`, `RETENTION.md`.
- `VACUUM FULL` and plain `REINDEX` can block traffic and are not approved. `REINDEX CONCURRENTLY` also requires disk, WAL and separate review.
- Backup timer schedule does not prove backup recoverability. Verify restore without exposing credentials.

### Safe evidence queries (read-only, timeout 10s)
```sql
SET default_transaction_read_only=on;
SET statement_timeout='10s';
SET lock_timeout='2s';
SELECT relname,pg_size_pretty(pg_total_relation_size(relid)),pg_size_pretty(pg_indexes_size(relid)),n_live_tup,n_dead_tup,last_autovacuum FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 20;
SELECT state,wait_event_type,wait_event,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY 1,2,3 ORDER BY count(*) DESC;
SELECT relname,indexrelname,idx_scan,pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_stat_user_indexes WHERE relname='processing_jobs' ORDER BY pg_relation_size(indexrelid) DESC;
SELECT pid,relid::regclass,phase,heap_blks_total,heap_blks_scanned FROM pg_stat_progress_vacuum;
```

### P0/P3 repeatable read-only claim evidence
Run `bash scripts/cam-pg-claim-baseline-readonly.sh 5 3` as the authorized VPS root operator. The script uses PostgreSQL peer authentication with read-only sessions, 7s statement timeout, and emits aggregate statistics only (no payloads, tenant IDs or query body). Treat `pg_stat_database` and `pg_stat_user_indexes` counters as cumulative: calculate deltas across timestamps. The claim-active count is a SQL-prefix heuristic, not a precise application trace; retain observations and corroborate with worker role source.

Blocker inventory (read-only, bounded by statement timeout): group only `status IN ('pending','retry')` by `job_type` and count `next_attempt_at<=now()`; separately join `tenant_provider_policies` using all three keys to measure provider blocks. Match incident-held IDs through `cam_incident_20261024_job_hold.job_id=processing_jobs.id`; do not modify the 2099 scheduling sentinel. The `_managed_storage_capacity_available` gate for `asset_store` is an independent staging-backpressure condition, not proof of cap denial. Do not infer all due asset_store jobs are runnable.

### P3 read-only SQLAlchemy candidate EXPLAIN (2026-10-25)
The source-aware diagnostic `scripts/cam_pg_claim_plan_readonly.py` constructs the **application claimer's actual candidate SELECT** (not the subsequent UPDATE or reservation) through a capture-only Session. It compiles with PostgreSQL dialect and submits **EXPLAIN (FORMAT JSON), without ANALYZE** using PostgreSQL peer authentication and forced `default_transaction_read_only=on`, 6-second statement timeout, 1-second lock timeout. The diagnostic reports only planner aggregates; it never prints source SQL, database passwords, tenant IDs or job payloads. Its `Settings.model_construct` values, Managed Storage folder and role allowlists are synthetic, so estimates are not a production A/B result and cannot validate actual runtime settings/preferred-job subset or selected job equality.

Run as authorized root on the VPS from repo root:
```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_claim_plan_readonly.py --role image
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_claim_plan_readonly.py --role visual
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_claim_plan_readonly.py --role video-heavy
PYTHONPATH=apps/api:. apps/api/.venv/bin/python -m unittest scripts.test_cam_pg_claim_plan_readonly -v
```

Observed 2026-10-25 plan estimates (PostgreSQL 16, synthetic settings):

| Role | Planner estimated total cost | Sort nodes | Main index examples |
|---|---:|---:|---|
| image | 1,450,150.05 | 1 | `ix_processing_jobs_available`, `ix_processing_jobs_policy_claim`, `ix_processing_jobs_due_eligible` |
| visual | 1,229,794.68 | 1 | `ix_processing_jobs_available`, `ix_processing_jobs_policy_claim` |
| video-heavy | 20,125.22 | 1 | `ix_processing_jobs_policy_claim` |

These numbers are **relative planner cost units for different role workloads, not milliseconds, CPU consumption or actual measured rows/buffers**. No evidence supports an index DROP or increase to work_mem yet. The query's large fairness `CASE` expression remains under Sort; retain its exact ordering. P4 requires an isolated restored clone, real worker runtime settings and measured A/B (same job ID and two-session SKIP LOCKED), not this tool alone.

### Acceptance and rollback
Only promote after an A/B evidence record (same actual SQLAlchemy SQL, tenant/job equality, fairness, p50/p95, shared buffers, temp spill, CPU) and all relevant gates PASS. For a canary error or lock/IO increase, disable flag or revert release, verify read-only counts and production health; do not revert by restoring old pipeline rows blindly. Track each step with time, command, SHA, before/after, operator, evidence and rollback status.

### Execution log
- 2026-10-25: P0 read-only inventory **DONE initial**. DB 3,848 MB; 195 tables; 704 indexes; no deletions/mutations.
- 2026-10-25 11:29 +07 (execution pass 2): G2 disk **PASS snapshot only** (9.0 GiB available, 81% use; check again before every change). Backup timer active and last service run exited status 0 on 2026-10-23 22:01:50 +07; **G1 remains BLOCKED**, because independent remote checksum receipt and isolated restore PASS were not verified. G0 sampling remains IN PROGRESS. PostgreSQL has ~64,223 estimated dead tuples in processing_jobs, last autovacuum 2026-10-24 16:14+07; no setting changes. Service API active; observed one idle-in-transaction session, zero lock waits. No VACUUM/REINDEX/DELETE/ALTER attempted.
- 2026-10-25 12:03 +07 (execution pass 3): `pg_lsclusters` shows **only PostgreSQL 16 main, port 5432** online; no isolated restore cluster is present. Root volume remains ~9.0 GiB free (81% used); API and backup timer active; last backup service SUCCESS at 2026-10-23 22:01:50 +07. Source has an operator-only checksum-validating Google Drive downloader and guarded isolated restore verifier, but managed remote checksum verification and actual restore have **NOT** run in this pass. G1 **BLOCKED**, G2 **PASS snapshot only**, G0 sampling **IN PROGRESS**. Read-only activity sample at 12:03:09: 3 active, 0 lock waits, 0 idle-in-transaction. Cumulative `pg_stat_database` at 12:03:12: 40,924 temp files, 299,844,002,324 temp bytes, 76 deadlocks; these are not instantaneous rates. No production DB writes or schema changes.
- 2026-10-25 12:05 +07 (execution pass 4): `main` and `origin/main` both 26f292fb5; existing four unrelated UI smoke changes left untouched. Disk available ~9.1 GB, 81% used (G2 snapshot PASS). Backup timer active and last backup service success unchanged (2026-10-23 22:01:50+07); only `16/main` on port 5432 is registered, so no isolated restore PASS (G1 BLOCKED). PostgreSQL sample 12:05:40: 5 active, 0 lock waits, 0 idle-in-transaction. 12:05 vacuum estimates: `processing_jobs` 875,893 live, 66,600 dead, 70,531 changed since analyze, last autovacuum 2026-10-24 16:14:48+07; `asset_ai_analyses` 67,150 live, 9,462 dead, never autovacuum recorded. 12:05:50 cumulative temp files 40,924, temp bytes 299,844,002,324, deadlocks 76. These are counters, not rates. No mutation performed.
- 2026-10-25 12:09–12:12 +07 (execution pass 5): P0/P3 read-only five-sample, 12-second claim probe completed using `scripts/cam-pg-claim-baseline-readonly.sh`; active claim-prefix query counts 3/2/1/2/1, longest observed active claim age 1.92 seconds. Lock waits observed in 2/5 samples (one waiter each); 0 idle-in-transaction. `ix_processing_jobs_policy_claim.idx_tup_read` rose from 99,515,594,537 to 99,520,872,530 (delta 5,277,993 index tuple reads in 12 seconds); `pg_stat_database.blks_read` rose 666,095 blocks in the same window. Neither temp_files nor temp_bytes nor deadlocks rose in this sample. All are systemwide cumulative deltas, not per-query causal attribution or physical disk bytes. Read-only job census: 58,910 `search_index_sync` and 58,116 `visual_index_sync` pending (117,026 total) all have `next_attempt_at=2099-01-01+07`. Join against `cam_incident_20261024_job_hold` confirmed all 117,026 incident IDs still exist and remain held, zero missing: **PROTECT, DO NOT RELEASE**. Other due jobs: 6,057 `video_analyze` all blocked by disabled video provider policy; 3,352 due `asset_store` with no provider policy match and no active tenant cap denial (separate Managed Storage staging backpressure may apply). Tenant policy active, 1 of 2 provider policies globally blocked (scope video). This is candidate filtering evidence, not permission to override policy. `tenant_provider_policies` cumulative seq_scan ~899.9M but no meaningful change in a separate 4s sample; do not claim it is current hot loop from cumulative figure. Autovacuum default threshold for `processing_jobs` ~175,231 dead tuples vs observed ~66,731, explaining why vacuum may not trigger yet; this is not proof of an autovacuum fault. No query rewrites or DB mutation occurred.
- Decision: P0 timed sampling **DONE** for this pass; P3 root-cause shortlist **IN PROGRESS**. Prioritize measuring real claimant SQL and early policy rejection for video provider and due status, while preserving fairness, allowed job types, temporary 2099 holds and SKIP LOCKED; avoid indiscriminate index creation or claiming temp spills. G1 still BLOCKED because verified remote checksum and isolated restore PASS are missing. G2 has ~9.1 GB free at last check, not a substitute for restore headroom. Do not run P2/P4/P5/P6 mutations before hard gates.
- 2026-10-25 12:14-12:17+07 (execution pass 6): 3-sample read-only claim probe 12:14:57/12:14:59/12:15:01 had claim counts 2/1/3, max active claim age 1.71s, one lock waiter and one idle-in-transaction at first snapshot; later samples zero; cumulative temp files rose from prior 40,924 to 40,928, temp bytes from 299,844,002,324 to 299,861,508,628 (not attributable to a single query). Using source `TenantAwareJobClaimer._next_candidate` captured read-only SELECT with SKIP LOCKED and compiled three synthetic-role EXPLAIN plans, without ANALYZE/job claims; results in P3 section above. Diagnostic safety unit tests 2/2 PASS; existing `apps/api/tests/modules/processing_policy/test_policy_and_claim.py` suite 47/47 PASS. Exact runtime SQL and CPU/BUFFERS A/B remain unmeasured. Backup receipt/isolated restore still not confirmed G1 BLOCKED; no production DB writes, no policy changes.
- Next action: authorized managed-backup checksum + isolated restore on separate adequate-capacity environment; then A/B actual SQLAlchemy query (p50/p95, BUFFERS, CPU, selected job equality, two-session locking) before any production activation. Never infer restore PASS from timer/service success.
