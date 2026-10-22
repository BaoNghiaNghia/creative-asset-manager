# CAM VPS disk-pressure hardening — 2026-10-22

## Observed production facts and priority

At the read-only audit, root ext4 was 84% full (~7.7 GiB available).
PostgreSQL processing_jobs held ~638k rows / ~1.44 GB, with ~1.05 GB
in indexes. About 321,764 completed jobs older than 30 days still retained
nonempty payload_json. production.env explicitly had
RETENTION_CLEANUP_ENABLED=false, with zero retention_cleanup_runs.
The job table has live FK references and a tenant/idempotency unique key.

**P0 scope:** Do not delete job rows, drop their indexes or run VACUUM FULL.
Existing cleanup redacts payload data and preserves identity; it does NOT
shrink indexes or purge rows. Unbounded job-row growth remains a separate
schema/archival design requiring a reviewed migration and idempotency tombstones.

The new RETENTION_CLEANUP_RECORD_TYPES setting defaults to `all` for existing
compatibility. Select `completed_job_payloads` for a narrowly scoped production
canary. Unsupported, empty or repeated values fail without scheduling.
If creation of a cleanup run succeeds but enqueue of the cleanup job fails,
the orphaned run is marked failed so future schedules are not blocked.

## P0 rollout gates

1. Use the latest approved frontend/backend release, not an unrelated stale
   branch. Verify backups using the existing managed backup restore runbook.
   Confirm sufficient free space for temporary PostgreSQL WAL and vacuum
   churn (aim for at least 8 GiB, preferably more) before applying any
   multi-thousand-row updates.
2. Read-only census (never print job payloads):

   ```sql
   SELECT count(*) FROM processing_jobs
   WHERE status='completed'
     AND completed_at < now() - interval '30 days'
     AND payload_json::text <> '{}';
   SELECT job_type, status, count(*) FROM processing_jobs
   GROUP BY job_type, status ORDER BY count(*) DESC;
   SELECT pg_size_pretty(pg_total_relation_size('processing_jobs'));
   ```

3. Production canary variables (requires an approved, reviewed config rollout):

   ```text
   RETENTION_CLEANUP_ENABLED=true
   RETENTION_CLEANUP_RECORD_TYPES=completed_job_payloads
   RETENTION_CLEANUP_BATCH_SIZE=100
   RETENTION_CLEANUP_MAX_ROWS=1000
   RETENTION_CLEANUP_INTERVAL_SECONDS=3600
   ```

   The initial rate must be verified on a small test tenant or a known
   limited production window. The setting does not clean sessions, raw AI
   responses, source-sync runs, logs, or outbox rows in this canary.
   Do not enable the default `all` scope just to free disk quickly.
4. Verify `retention_cleanup_runs` progress, bounded row counts, job status,
   WAL growth, available bytes, API latency, and autovacuum. Disable the
   feature flag and stop scheduling if available space/latency worsens.
5. Measure real disk recovery. PostgreSQL may reuse internal free space after
   redaction without reducing the `df` value. Any REINDEX CONCURRENTLY,
   VACUUM FULL or table partitioning needs a separate reviewed plan.
   Do not bypass live FKs or idempotency for the sake of a smaller database.

## P0 lightweight monitoring

`scripts/cam-disk-watch.py` uses only statvfs (not a root filesystem walk)
and emits JSON with thresholds 80% notice, 85% warning, 90% critical,
95% emergency. It does not delete anything or access secrets/SQL.
The optional `deploy/systemd/creative-asset-manager-disk-watch.service`
and `.timer` run this read-only check every 15 minutes.

After an approved release, install/enable the two units using the existing
reviewed non-root production deployment process, then verify with
`systemctl list-timers` and
`journalctl -u creative-asset-manager-disk-watch.service -n 10`.
The timer is not activated by committing source files.

For a full *on-demand* attribution report, use the existing
`scripts/cam-disk-growth-audit.sh --save` from an authorized VPS terminal.
Its full-root scan is deliberately **not** run every 15 minutes.

## P1 transient file guards

- Codex private generated_images remains transient; final outputs/checkpoints
  needed for Drive retry are preserved. Failed cache deletion now emits an
  error-type-only warning, not a path, token or payload.
- cleanup_attempt rejects empty, traversal, absolute and symlink targets
  rather than removing an arbitrary path. The two shared account-level
  generated_images caches (88 unverified historical PNGs at audit time)
  remain protected. The verified-only Drive reconciler stays unchanged.
- Video proxy's stale-workspace scanner now only counts bytes if the
  deletion succeeded; it skips links and logs permission failures without
  leaking paths. No new automatic deletion of video final/checkpoint files
  was introduced.

## P2 Google Drive read resiliency

Stage 1 legacy preview retention retries only transient GET/read exceptions,
up to three attempts with bounded delays; no retry on DELETE or non-retryable
4xx. Ownership, folder and age checks remain required before any Drive delete.
This cleanup affects Drive legacy drafts, not VPS disk bytes.

## Security, verification and rollback

No migration, new Python dependency or secret is required by the code patch.
No production files, database rows, generated images, backups, search indexes,
tenant data or sessions should be manually purged as part of rollout.

Targeted tests: scheduler canary/invalid selection/stuck-run recovery;
Codex path and permission guards; video proxy deletion accounting; Drive GET
retry/exhaustion. Confirm tests and production observations independently
before asserting deployment. Rollback is an immutable release pointer rollback,
plus RETENTION_CLEANUP_ENABLED=false if the pilot was activated; do not
roll back an already performed data redaction by inventing old payloads.
