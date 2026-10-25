#!/usr/bin/env bash
# CAM PostgreSQL claim baseline. READ-ONLY: aggregates only, no SQL/payload/tenant ID logging.
set -Eeuo pipefail
SAMPLES="${1:-5}"
INTERVAL="${2:-3}"
if ! [[ "$SAMPLES" =~ ^[1-9][0-9]?$ ]] || (( SAMPLES > 12 )) ||
   ! [[ "$INTERVAL" =~ ^[1-9][0-9]?$ ]] || (( INTERVAL > 10 )); then
  printf 'usage: %s [samples:1-12] [interval_seconds:1-10]\n' "$0" >&2
  exit 2
fi
if [[ "$(id -u)" != "0" ]]; then
  echo "Must run as root for peer-authenticated, read-only postgres stats." >&2
  exit 2
fi
command -v runuser >/dev/null
command -v psql >/dev/null
DB="creative_asset_manager"
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=7000 -c lock_timeout=1500'
echo "OBSERVED_AT_LOCAL|CLAIM_ACTIVE|CLAIM_MAX_SECONDS|ACTIVE|WAITING_LOCK|IDLE_IN_TX|TEMP_FILES_CUMULATIVE|TEMP_BYTES_CUMULATIVE|DEADLOCKS_CUMULATIVE|BLOCKS_READ_CUMULATIVE|BLOCKS_HIT_CUMULATIVE|POLICY_INDEX_TUPLES_READ_CUMULATIVE|AVAILABLE_INDEX_TUPLES_READ_CUMULATIVE|JOBS_DEAD_ESTIMATE"
for (( n=1; n<=SAMPLES; n++ )); do
  runuser -u postgres -- psql -X -A -F '|' -t -v ON_ERROR_STOP=1 -d "$DB" <<'SQL'
WITH activity AS (
  SELECT
    count(*) FILTER (WHERE state='active' AND pid <> pg_backend_pid()) AS active_count,
    count(*) FILTER (WHERE state='active' AND pid <> pg_backend_pid() AND query LIKE 'SELECT processing_jobs.id%') AS claims,
    coalesce(round(max(extract(epoch from clock_timestamp()-query_start)::numeric) FILTER (WHERE state='active' AND pid <> pg_backend_pid() AND query LIKE 'SELECT processing_jobs.id%'),2),0) AS max_claim_age_s,
    count(*) FILTER (WHERE wait_event_type='Lock') AS lock_waits,
    count(*) FILTER (WHERE state='idle in transaction') AS idle_transactions
  FROM pg_stat_activity
  WHERE datname=current_database()
),
indices AS (
  SELECT coalesce(sum(idx_tup_read) FILTER (WHERE indexrelname='ix_processing_jobs_policy_claim'),0) AS policy_reads,
         coalesce(sum(idx_tup_read) FILTER (WHERE indexrelname='ix_processing_jobs_available'),0) AS available_reads
  FROM pg_stat_user_indexes
),
jobstats AS (
  SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname='processing_jobs'
)
SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Ho_Chi_Minh','YYYY-MM-DD"T"HH24:MI:SS'),
       activity.claims, activity.max_claim_age_s, activity.active_count,activity.lock_waits,activity.idle_transactions,
       db.temp_files,db.temp_bytes,db.deadlocks,db.blks_read,db.blks_hit,
       indices.policy_reads,indices.available_reads,jobstats.n_dead_tup
FROM activity CROSS JOIN pg_stat_database AS db CROSS JOIN indices CROSS JOIN jobstats
WHERE db.datname=current_database();
SQL
  if (( n < SAMPLES )); then sleep "$INTERVAL"; fi
done
