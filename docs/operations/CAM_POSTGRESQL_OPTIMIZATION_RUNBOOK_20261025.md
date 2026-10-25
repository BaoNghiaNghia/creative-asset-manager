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
| P1 | Check backup receipt, isolated restore, disk/WAL | PARTIAL: live Drive MD5/SHA-256/size metadata MATCH PASS (6 backups, matching file 327706668 bytes); stream re-download/isolated restore NOT RUN; same-volume capacity BLOCKED |
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

### P1 evidence, capacity and isolated restore handoff (2026-10-25)

The read-only command (authorized CodeLocal/VPS root; it **never restores** and does not print any digest or credential) is:

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_g1_preflight_readonly.py
PYTHONPATH=apps/api:. apps/api/.venv/bin/python -m unittest scripts.test_cam_pg_g1_preflight_readonly -v
```

Recorded live output: production database 4,036,271,127 bytes; root free 9,660,039,168 bytes. Local owner-private receipt built by the official backup service passed `has_recent_verified_receipt` (private metadata with SHA-256, independent Drive MD5 match and recent completion timestamp; full evidence remains private); **this does not mean a new download or restore passed**. Only PG16 cluster main port 5432 present. Same-volume estimate: another 7,128,148,515 bytes for restored data/WAL buffer plus retaining 8 GiB free after completion. Same-volume rehearsal is **BLOCKED** at current space. Use a separately provisioned PostgreSQL 16 host/volume with enough capacity; no `pg_createcluster` or restore on production volume.

Backup software tests: 50/50 backup/Drive/download/CLI PASS; 13/13 isolated restore guard tests PASS; 5/5 G1 preflight tests PASS. Unit tests prove guards, **not** a real restore.

CodeLocal read of the production environment file is a hard policy block even with full workspace access. Do **not** work around this through alternate paths/aliases or read credentials into workspace. The authorized root SSH operator must run the existing redacted production-env wrapper for a read-only managed-backup list and download, then restore only to a separately verified isolated target. The operator steps in `DATABASE_BACKUP.md` and `CATINAT_BACKUP_RESTORE_OPERATOR.md` apply, but use the *latest* verified managed backup, not the older Oct 20 fixed file.

Root SSH operator verification, outside CodeLocal workspace policy:
```bash
cd /opt/creative-asset-manager/current/apps/api
python3 /opt/creative-asset-manager/current/deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env --expected-owner-uid 0 -- \
  /opt/creative-asset-manager/current/apps/api/.venv/bin/python \
  -m app.operations.database_backup_cli verify-config
python3 /opt/creative-asset-manager/current/deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env --expected-owner-uid 0 -- \
  /opt/creative-asset-manager/current/apps/api/.venv/bin/python \
  -m app.operations.database_backup_cli list
```

The source `main` additionally provides `app.operations.cam_pg_g1_remote_metadata`, a read-only metadata matcher that prints **no** backup ID or checksum. It compares a valid recent local receipt against a uniquely matching Google Drive backup by content-derived MD5, SHA-256 appProperty, size, managed kind, and parent folder. The operator can run it via the same approved redacted production-env wrapper from a root SSH session, after first entering the CAM repository root (`/srv/creative-asset-manager-source`), then the `main` checkout's `apps/api` directory:

```bash
cd /srv/creative-asset-manager-source/.cam-gatorhats-fix-20261024/apps/api
python3 /opt/creative-asset-manager/current/deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env --expected-owner-uid 0 -- \
  /srv/creative-asset-manager-source/.cam-gatorhats-fix-20261024/apps/api/.venv/bin/python \
  -m app.operations.cam_pg_g1_remote_metadata
```

**Operator evidence received 2026-10-25:** `G1_LOCAL_RECENT_RECEIPT=PASS`, `G1_DRIVE_CONTENT_MD5_METADATA_MATCH=PASS`, `G1_DRIVE_SIZE_AND_SHA_PROPERTY_MATCH=PASS`, `MANAGED_BACKUP_COUNT=6`, `MATCHED_BACKUP_BYTES=327706668`, `DATABASE_MUTATIONS=NONE`, `G1_STREAM_REDOWNLOAD=NOT_RUN`, `G1_ISOLATED_RESTORE=NOT_RUN`, `G1_RECOVERY_GATE=BLOCKED`. These are verified remote metadata matches, **not** proof that the downloaded dump is restorable. G1 remains BLOCKED. The operation is deliberately unavailable to the CodeLocal workspace because production.env access is hard-blocked. If any required metadata is missing, if no unique backup matches, or if the remote provider cannot be accessed, it fails closed. No production API/worker deployment is required for this operator-only tool.

### Next step — download to an independently mounted volume, no manual backup ID

As of 2026-10-25 `lsblk` shows only `/dev/vda1` for the 50 GB production root filesystem. **Provision a new dedicated >=25 GiB volume or separate PG16 host first**; do not reformat or delete an existing production device. The new operator-only module `app.operations.cam_pg_g1_download_matched` selects exactly the unique Drive file matching the verified local receipt, streams it, and hashes the bytes again. It does not take a user-supplied remote file ID and never prints one, or any OAuth token. It fails closed unless the target is a root-owned 0700 directory on a different mounted filesystem with >=20 GiB currently free. No restore, production database writes, disk formatting, or remote deletion occurs.

Once an external volume is *already safely mounted* at `/mnt/cam-pg-restore`, start at the CAM repository root (`/srv/creative-asset-manager-source`) and run these instructions from a trusted root SSH terminal, **not CodeLocal**:

```bash
set -Eeuo pipefail
MOUNT=/mnt/cam-pg-restore
mountpoint -q "$MOUNT" || { echo "BLOCKED: separate volume not mounted"; exit 2; }
test "$(stat -c '%d' "$MOUNT")" != "$(stat -c '%d' /)" || { echo "BLOCKED: destination shares production root device"; exit 2; }
df -h "$MOUNT"
install -d -m 700 -o root -g root "$MOUNT/private-backups"
cd /srv/creative-asset-manager-source/.cam-gatorhats-fix-20261024/apps/api
python3 /opt/creative-asset-manager/current/deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env --expected-owner-uid 0 -- \
  /srv/creative-asset-manager-source/.cam-gatorhats-fix-20261024/apps/api/.venv/bin/python \
  -m app.operations.cam_pg_g1_download_matched --private-directory "$MOUNT/private-backups"
```

Expected `G1_DRIVE_STREAM_VERIFIED=PASS` and `G1_DOWNLOAD_ON_SEPARATE_VOLUME=PASS`, with `G1_ISOLATED_RESTORE=NOT_RUN`. Keep the downloaded file and checksum evidence **private**. Next, provision a *separate* PostgreSQL 16 data directory and isolated listener on that external volume or another host (never production port 5432), with a non-superuser owner, empty target DB and immutable private evidence; use the existing guarded `scripts/cam_db_restore_rehearsal.py` with the freshly downloaded SHA-256/MD5. The older fixed-path Catinat 2026-10-20 restore script must **not** be used for this current backup or the root disk. Independently compare Alembic revision, core table counts, recovered assets, completed jobs and 117026 incident-held jobs at the *snapshot timestamp*, then measure restore time (RTO), backup freshness (RPO) and validate rollback. Do not promote G1 until an actual `RESTORE_REHEARSAL=PASS` is recorded and reviewed.

If the external volume is unavailable, **stop here**. Under no circumstances use `/dev/shm`, temporary overlay filesystems, the production root volume or an unreviewed alternate path to bypass the mount/capacity gate.



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
- 2026-10-25 (execution pass 7, P1): official nonsecret receipt validator returned `BACKUP_REMOTE_RECEIPT_RECENT=True` for `/var/lib/creative-asset-manager/database-backup`. A new read-only `scripts/cam_pg_g1_preflight_readonly.py` returned `backup_local_recent_receipt=PASS`, database 4,036,271,127 bytes, root free 9,660,039,168 bytes, estimated additional same-volume restore need 7,128,148,515 bytes plus an 8-GiB free-space floor. Only PG16 main/5432 exists: **same-volume restore BLOCKED**; do not create a clone there. Production protected env access is CodeLocal hard-blocked, so no new Drive list/download/checksum was performed and no actual restore was performed. Added operator-only, read-only `app.operations.cam_pg_g1_remote_metadata` to independently compare receipt with current managed Drive metadata in an authorized root SSH session without displaying IDs/digests; it was **unit-tested only, not executed against live Drive**. Tests: 50 backup/CLI/download, 3 new remote metadata, 13 restore guard, and 5 preflight = 71 PASS. P1 remains PARTIAL/BLOCKED until remote check plus separate-volume/host restore and row/revision verification.
- 2026-10-25 (execution pass 8, P1): user-operated root SSH verified **live** Managed Google Drive backup metadata: local recent receipt PASS, remote MD5 content metadata PASS, SHA-256 appProperty and byte size PASS; 6 managed backups, unique matching backup size 327,706,668 bytes (~313 MiB), no database mutation. Streaming re-download and independent restore both NOT_RUN, so **G1 remains BLOCKED**. Read-only inventory of attached devices: only /dev/vda1 production root; no external restore volume. Implemented fail-closed `app.operations.cam_pg_g1_download_matched` that selects the unique receipt-matched backup internally and only streams it onto an existing root-private folder on a different mounted device with >=20 GiB free; no remote ID/digest/token logged, and no database claim or schema mutation. Verified application backup/download/metadata tests 59/59 PASS and restore/preflight guards 18/18 PASS; the new downloader was unit-tested, **not run against live Drive**. Updated exact runbook steps for an operator-supplied >=25 GiB external volume, leaving production root unchanged.
- Next action: provision or connect an independent >=25 GiB volume (or a separate PG16 host) under authorized infrastructure operations. Only then use the new operator-only downloader to verify streamed bytes, restore to an isolated non-superuser PG16 database, and compare source revision/representative row counts; record RPO/RTO, validate rollback and mark G1 PASS before activating P2/P4 changes. Do not run restore against production 5432 or reduce production free-space floor.
