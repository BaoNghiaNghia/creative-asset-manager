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
| P0 | Read-only database/table/index/lock/vacuum inventory | DONE initial snapshot; samples pending |
| P1 | Check backup receipt, isolated restore, disk/WAL | BLOCKED pending evidence |
| P2 | Autovacuum hygiene: derive per-table dead tuple threshold; consider narrow reloptions; VACUUM (ANALYZE) only when safe | BLOCKED by G1/G2 |
| P3 | Diagnose real SQLAlchemy claim query, rejection groups, blocked group backoff and eligible tenant/job_type pairs | PENDING |
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

### Acceptance and rollback
Only promote after an A/B evidence record (same actual SQLAlchemy SQL, tenant/job equality, fairness, p50/p95, shared buffers, temp spill, CPU) and all relevant gates PASS. For a canary error or lock/IO increase, disable flag or revert release, verify read-only counts and production health; do not revert by restoring old pipeline rows blindly. Track each step with time, command, SHA, before/after, operator, evidence and rollback status.

### Execution log
- 2026-10-25: P0 read-only inventory **DONE**. DB 3,848 MB; 195 tables; 704 indexes; no deletions/mutations. Backup timer discovered; not verified as restored. P1–P7 require their gates before action.
