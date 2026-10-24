BEGIN;
SET LOCAL statement_timeout='25s';
SET LOCAL lock_timeout='4s';

ALTER TABLE cam_incident_20261024_source_reuse
 ADD COLUMN IF NOT EXISTS original_store_job_id varchar(36);
ALTER TABLE cam_incident_20261024_source_reuse
 ADD COLUMN IF NOT EXISTS original_store_due timestamptz;
ALTER TABLE cam_incident_20261024_source_reuse
 ADD COLUMN IF NOT EXISTS original_new_pipeline_state varchar(40);

WITH selected AS MATERIALIZED (
 SELECT m.new_source_id,m.old_source_id,m.new_job_id,
 m.original_asset_id,m.original_pipeline_id,p.id current_pipeline_id,
 p.status_data_json original_status_json,st.id store_job_id,
 st.next_attempt_at store_due,
 oldp.analysis_id,oldp.content_hash,oldp.projection_version,
 oldp.projection_checksum,oldp.indexed_projection_version,
 oldp.indexed_projection_checksum
 FROM cam_incident_20261024_source_reuse m
 JOIN source_assets new_sa ON new_sa.id=m.new_source_id AND new_sa.tenant_id='creative-assets-prod'
 JOIN processing_jobs j ON j.id=m.new_job_id AND j.tenant_id='creative-assets-prod'
 JOIN asset_pipelines p ON p.tenant_id='creative-assets-prod' AND p.origin_type='source_asset'
  AND p.origin_id=m.new_source_id
 JOIN processing_jobs st ON st.tenant_id=p.tenant_id AND st.entity_id=p.id
  AND st.job_type='asset_store'
 JOIN asset_source_links sl ON sl.tenant_id='creative-assets-prod'
  AND sl.source_asset_id=m.new_source_id AND sl.asset_id=m.original_asset_id
 JOIN asset_pipelines oldp ON oldp.tenant_id='creative-assets-prod' AND oldp.id=m.original_pipeline_id
 JOIN asset_ai_analyses aa ON aa.id=oldp.analysis_id AND aa.tenant_id=oldp.tenant_id
 JOIN assets a ON a.id=m.original_asset_id AND a.tenant_id='creative-assets-prod'
 WHERE m.restored_at IS NULL AND m.original_pipeline_id IS NOT NULL
 AND j.status='completed' AND j.job_type='source_asset_download'
 AND p.state='storage_pending' AND st.status='pending'
 AND st.cancellation_requested IS FALSE AND st.attempt_count<st.max_attempts
 AND p.asset_id=m.original_asset_id AND p.content_hash=a.content_hash
 AND oldp.state='completed' AND oldp.asset_id=a.id
 AND oldp.analysis_id=aa.id AND aa.status='completed'
 AND oldp.indexed_projection_version IS NOT NULL
 AND oldp.indexed_projection_checksum IS NOT NULL
 AND aa.asset_id=a.id AND new_sa.deleted_at IS NULL
 AND a.size_bytes=new_sa.size_bytes AND a.mime_type=new_sa.mime_type
 AND m.method='sha256+graph-etag'
 AND LOWER(new_sa.provider_checksum)=a.content_hash
 LIMIT 50 FOR UPDATE OF m,p,st SKIP LOCKED
), cancel_store AS (
 UPDATE processing_jobs j
 SET status='completed',cancellation_requested=false,
 payload_json=(coalesce(j.payload_json,'{}'::json)::jsonb ||
 jsonb_build_object('reused_existing_asset',true,
 'store_action','skipped_duplicate_existing_completed_pipeline'))::json,
 next_attempt_at=now(),updated_at=now(),completed_at=now(),
 last_error_code=NULL,last_error_message=NULL
 FROM selected s WHERE j.id=s.store_job_id
 RETURNING j.id
), finish_pipeline AS (
 UPDATE asset_pipelines p
 SET state='completed',analysis_id=s.analysis_id,content_hash=s.content_hash,
 projection_version=s.projection_version,projection_checksum=s.projection_checksum,
 indexed_projection_version=s.indexed_projection_version,
 indexed_projection_checksum=s.indexed_projection_checksum,
 last_error_code=NULL,last_error_message=NULL,failure_retryable=NULL,
 status_data_json=(coalesce(p.status_data_json,'{}'::json)::jsonb ||
 jsonb_build_object('recovered_from_existing_asset',true,
 'incident_run_id','64f21db4-3e45-4843-bffd-afc4fb44cef1',
 'method','sha256+graph-etag','previous_pipeline_id',s.original_pipeline_id))::json,
 updated_at=now(),completed_at=now()
 FROM selected s JOIN cancel_store c ON c.id=s.store_job_id
 WHERE p.id=s.current_pipeline_id RETURNING p.id,p.origin_id
), audit AS (
 UPDATE cam_incident_20261024_source_reuse m
 SET restored_pipeline_id=p.id,restored_at=now(),
 original_store_job_id=s.store_job_id,original_store_due=s.store_due,
 original_new_pipeline_state='storage_pending'
 FROM finish_pipeline p JOIN selected s ON s.current_pipeline_id=p.id
 WHERE m.new_source_id=s.new_source_id RETURNING m.new_source_id
)
SELECT count(*) restored_existing_downloads FROM audit;
COMMIT;
