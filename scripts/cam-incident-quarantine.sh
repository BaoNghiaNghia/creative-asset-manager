#!/usr/bin/env bash
# Quarantine only the mass retirement/index-sync jobs from a verified bad
# OneDrive full-reconciliation run. All original due dates are preserved in
# cam_incident_20261024_job_hold for an exact later release/rollback.
set -Eeuo pipefail
TENANT="creative-assets-prod"
RUN="64f21db4-3e45-4843-bffd-afc4fb44cef1"
BATCH_SIZE=1500
MAX_BATCHES=110
HOLD_UNTIL="2026-11-02 00:00:00+07"
MODE="${1:---dry-run}"
[[ "$MODE" == "--dry-run" || "$MODE" == "--apply" ]] || { echo "usage: $0 [--dry-run|--apply]" >&2; exit 2; }
PSQL=(runuser -u postgres -- psql -d creative_asset_manager -v ON_ERROR_STOP=1 -P pager=off -At)
VERIFY="${PSQL[*]}"
info="$("${PSQL[@]}" -c "SELECT status || '|' || mode || '|' || jobs_created_count FROM source_sync_runs WHERE id='$RUN' AND tenant_id='$TENANT';")"
[[ "$info" == "completed|full|"* ]] || { echo "Incident run not found or not completed" >&2; exit 2; }
count="${info##*|}"
(( count >= 50000 )) || { echo "Incident count differs; refusing" >&2; exit 2; }
echo "Verified incident run=$RUN created_jobs=$count; mode=$MODE"
query="SELECT job_type,count(*) FROM processing_jobs
 WHERE tenant_id='$TENANT' AND status='pending' AND next_attempt_at <= now()
 AND (
 (job_type='visual_index_sync' AND idempotency_key LIKE 'visual-retire:%:reconciliation:$RUN:visual_embedding_v2')
 OR (job_type='search_index_sync' AND idempotency_key LIKE 'source-index-sync:%:reconciliation:$RUN'))
 GROUP BY job_type ORDER BY job_type;"
"${PSQL[@]}" -c "$query"
[[ "$MODE" == "--apply" ]] || exit 0
total=0
for ((i=1; i<=MAX_BATCHES; i++)); do
  sql="BEGIN; SET LOCAL lock_timeout='4s'; SET LOCAL statement_timeout='35s';
  WITH chosen AS MATERIALIZED (
    SELECT j.id,j.tenant_id,j.job_type,j.status,j.priority,j.next_attempt_at
    FROM processing_jobs j WHERE j.tenant_id='$TENANT'
    AND j.status='pending' AND j.next_attempt_at<=now()
    AND j.cancellation_requested IS FALSE
    AND (
     (j.job_type='visual_index_sync' AND j.idempotency_key LIKE 'visual-retire:%:reconciliation:$RUN:visual_embedding_v2')
      OR (j.job_type='search_index_sync' AND j.idempotency_key LIKE 'source-index-sync:%:reconciliation:$RUN')
    )
    ORDER BY j.created_at,j.id LIMIT $BATCH_SIZE FOR UPDATE OF j SKIP LOCKED
  ), saved AS (
    INSERT INTO cam_incident_20261024_job_hold
    (job_id,tenant_id,job_type,original_next_attempt_at,original_priority,original_status,held_until,incident_run_id)
    SELECT id,tenant_id,job_type,next_attempt_at,priority,status,
      '$HOLD_UNTIL'::timestamptz,'$RUN' FROM chosen
    ON CONFLICT (job_id) DO NOTHING RETURNING job_id
  ), moved AS (
    UPDATE processing_jobs j SET next_attempt_at='$HOLD_UNTIL'::timestamptz
    FROM saved s WHERE j.id=s.job_id AND j.status='pending'
    RETURNING j.id
  )
  SELECT count(*) FROM moved;
  COMMIT;"
  # Suppress psql BEGIN/COMMIT status only. SQLSTATE failures stop the script.
  output="$("${PSQL[@]}" -q -c "$sql")"
  n="$(printf '%s\n' "$output" | grep -E '^[0-9]+$' | tail -n 1)"
  [[ -n "$n" ]] || { echo "Unexpected SQL output: $output" >&2; exit 2; }
  total=$((total+n))
  if (( i % 10 == 0 || n < BATCH_SIZE )); then
    echo "hold_batch=$i batch_rows=$n total_held=$total"
  fi
  (( n > 0 )) || break
done
echo "FINISHED: newly_held=$total"
"${PSQL[@]}" -c "SELECT job_type,count(*) FROM cam_incident_20261024_job_hold WHERE incident_run_id='$RUN' GROUP BY 1 ORDER BY 1;"
