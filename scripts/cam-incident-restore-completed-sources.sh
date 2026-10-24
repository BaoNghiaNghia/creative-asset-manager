#!/usr/bin/env bash
# Restore only proven identical OneDrive files to their existing immutable
# asset, completed analysis and index, without fetching source bytes or AI.
set -Eeuo pipefail
TENANT="creative-assets-prod"
RUN="64f21db4-3e45-4843-bffd-afc4fb44cef1"
MODE="${1:---dry-run}"
BATCH_SIZE="${2:-500}"
MAX_BATCHES="${3:-130}"
[[ "$MODE" == "--dry-run" || "$MODE" == "--apply" ]] || exit 2
[[ "$BATCH_SIZE" =~ ^[0-9]+$ && "$MAX_BATCHES" =~ ^[0-9]+$ ]] || exit 2
(( BATCH_SIZE >= 1 && BATCH_SIZE <= 1000 && MAX_BATCHES >= 1 && MAX_BATCHES <= 500 )) || exit 2
PSQL=(runuser -u postgres -- psql -d creative_asset_manager -P pager=off -v ON_ERROR_STOP=1 -At)
echo "INCIDENT=$RUN mode=$MODE batch=$BATCH_SIZE max_batches=$MAX_BATCHES"
"${PSQL[@]}" -c "SELECT method,count(*) eligible FROM cam_incident_20261024_source_reuse
WHERE original_pipeline_id IS NOT NULL AND restored_at IS NULL
GROUP BY 1 ORDER BY 1;"
[[ "$MODE" == "--apply" ]] || exit 0
total=0
for ((batch=1; batch<=MAX_BATCHES; batch++)); do
sql="BEGIN;
SET LOCAL lock_timeout='4s';
SET LOCAL statement_timeout='35s';
WITH front AS MATERIALIZED (
 SELECT m.new_source_id,m.new_job_id,m.old_source_id,
        m.original_asset_id,m.original_pipeline_id,m.method
 FROM cam_incident_20261024_source_reuse m
 JOIN processing_jobs j ON j.id=m.new_job_id AND j.tenant_id='$TENANT'
 WHERE m.restored_at IS NULL AND m.original_pipeline_id IS NOT NULL
 AND j.status='pending' AND j.job_type='source_asset_download'
 AND j.cancellation_requested IS FALSE AND j.attempt_count<j.max_attempts
 AND NOT EXISTS (SELECT 1 FROM asset_source_links sl
                 WHERE sl.tenant_id='$TENANT' AND sl.source_asset_id=m.new_source_id)
 AND NOT EXISTS (SELECT 1 FROM asset_pipelines duplicate
                 WHERE duplicate.tenant_id='$TENANT'
                 AND duplicate.origin_type='source_asset'
                 AND duplicate.origin_id=m.new_source_id)
 ORDER BY m.new_source_id LIMIT $BATCH_SIZE
 FOR UPDATE OF m,j SKIP LOCKED
), chosen AS MATERIALIZED (
 SELECT m.new_source_id source_id,m.new_job_id job_id,m.old_source_id,
 m.original_asset_id asset_id,m.original_pipeline_id old_pipeline_id,
 m.method,j.priority job_priority,j.next_attempt_at job_due,
 p.analysis_id,p.content_hash,p.projection_version,p.projection_checksum,
 p.indexed_projection_version,p.indexed_projection_checksum
 FROM front m
 JOIN processing_jobs j ON j.id=m.new_job_id AND j.tenant_id='$TENANT'
 JOIN source_assets src ON src.id=m.new_source_id AND src.tenant_id='$TENANT'
 JOIN source_assets old_src ON old_src.id=m.old_source_id AND old_src.tenant_id='$TENANT'
 JOIN asset_pipelines p ON p.id=m.original_pipeline_id AND p.tenant_id='$TENANT'
 JOIN asset_ai_analyses ai ON ai.id=p.analysis_id AND ai.tenant_id='$TENANT'
 JOIN assets a ON a.id=m.original_asset_id AND a.tenant_id='$TENANT'
 JOIN external_sources es ON es.id=src.external_source_id AND es.tenant_id='$TENANT'
 JOIN oauth_connections oc ON oc.id=es.oauth_connection_id AND oc.tenant_id='$TENANT'
 WHERE j.job_type='source_asset_download' AND j.entity_type='source_asset'
 AND j.entity_id=m.new_source_id AND j.status='pending'
 AND j.cancellation_requested IS FALSE AND j.attempt_count<j.max_attempts
 AND src.deleted_at IS NULL AND es.status='active' AND oc.status='active'
 AND oc.revoked_at IS NULL
 AND old_src.deleted_at='2026-10-24 01:38:25.357732+07'
 AND p.state='completed' AND p.asset_id=m.original_asset_id
 AND p.content_hash=a.content_hash AND p.analysis_id=ai.id
 AND ai.asset_id=a.id AND ai.status='completed'
 AND p.indexed_projection_version IS NOT NULL
 AND p.indexed_projection_checksum IS NOT NULL
 AND a.size_bytes=src.size_bytes AND a.mime_type=src.mime_type
 AND src.provider_version=old_src.provider_version
 AND (
  (m.method='sha256+graph-etag' AND LOWER(src.provider_checksum)=a.content_hash)
  OR (m.method='graph-item-etag'
      AND src.provider_checksum IS NULL
      AND old_src.provider_checksum IS NULL)
 )
 AND NOT EXISTS (SELECT 1 FROM asset_source_links sl
  WHERE sl.tenant_id='$TENANT' AND sl.source_asset_id=src.id)
 AND NOT EXISTS (SELECT 1 FROM asset_pipelines duplicate
  WHERE duplicate.tenant_id='$TENANT'
  AND duplicate.origin_type='source_asset' AND duplicate.origin_id=src.id)
 FOR UPDATE OF src SKIP LOCKED
), links AS (
 INSERT INTO asset_source_links
 (id,tenant_id,asset_id,source_asset_id,created_at)
 SELECT gen_random_uuid()::text,'$TENANT',asset_id,source_id,now()
 FROM chosen
 ON CONFLICT (tenant_id,source_asset_id) DO NOTHING
 RETURNING source_asset_id
), pipelines AS (
 INSERT INTO asset_pipelines
 (id,tenant_id,correlation_id,origin_type,origin_id,source_asset_id,
 asset_id,analysis_id,state,content_hash,projection_version,projection_checksum,
 indexed_projection_version,indexed_projection_checksum,status_data_json,
 created_at,updated_at,completed_at)
 SELECT gen_random_uuid()::text,'$TENANT',
 'source_asset:'||c.source_id,'source_asset',c.source_id,c.source_id,
 c.asset_id,c.analysis_id,'completed',c.content_hash,c.projection_version,
 c.projection_checksum,c.indexed_projection_version,c.indexed_projection_checksum,
 json_build_object('recovered_from_existing_asset',true,
 'incident_run_id','$RUN','method',c.method,
 'previous_pipeline_id',c.old_pipeline_id),
 now(),now(),now()
 FROM chosen c JOIN links l ON l.source_asset_id=c.source_id
 ON CONFLICT (tenant_id,origin_type,origin_id) DO NOTHING
 RETURNING id,origin_id
), completed_jobs AS (
 UPDATE processing_jobs j
 SET status='completed',completed_at=now(),updated_at=now(),
 last_error_code=NULL,last_error_message=NULL,processing_duration_ms=0,
 claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL,concurrency_accounted=FALSE
 FROM pipelines p JOIN chosen c ON c.source_id=p.origin_id
 WHERE j.id=c.job_id AND j.tenant_id='$TENANT'
 AND j.status='pending'
 RETURNING j.entity_id source_id
), marked_sources AS (
 UPDATE source_assets src
 SET hashed_provider_checksum=src.provider_checksum,
 hashed_provider_version=src.provider_version,
 updated_at=now()
 FROM completed_jobs c
 WHERE src.id=c.source_id AND src.tenant_id='$TENANT'
 RETURNING src.id source_id
), audit AS (
 UPDATE cam_incident_20261024_source_reuse m
 SET original_job_due=c.job_due,
 original_job_priority=c.job_priority,
 restored_pipeline_id=p.id,
 restored_at=now()
 FROM marked_sources s
 JOIN chosen c ON c.source_id=s.source_id
 JOIN pipelines p ON p.origin_id=s.source_id
 WHERE m.new_source_id=s.source_id
 RETURNING m.method
)
SELECT method,count(*) n FROM audit GROUP BY method ORDER BY method;
COMMIT;"
  output="$("${PSQL[@]}" -q -c "$sql")"
  counts="$(printf '%s\n' "$output" | grep -E '^(sha256\+graph-etag|graph-item-etag)\|[0-9]+$' || true)"
  n="$(printf '%s\n' "$counts" | awk -F'|' '{sum+=$2} END {print sum+0}')"
  total=$((total+n))
  if (( batch % 10 == 0 || n < BATCH_SIZE )); then
    echo "batch=$batch restored=$n total=$total methods=$counts" | tr '\n' ' '
    echo
  fi
  (( n > 0 )) || break
done
echo "FINISHED restored_in_this_run=$total"
"${PSQL[@]}" -c "SELECT method, count(*) FILTER(WHERE restored_at IS NOT NULL) restored,
count(*) FILTER(WHERE original_pipeline_id IS NOT NULL AND restored_at IS NULL) remaining
FROM cam_incident_20261024_source_reuse GROUP BY 1 ORDER BY 1;"
