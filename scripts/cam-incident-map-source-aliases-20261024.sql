-- Emergency 2026-10-24 OneDrive source alias map; no job status changes.
BEGIN;
SET LOCAL lock_timeout='4s';
SET LOCAL statement_timeout='55s';
SET LOCAL enable_nestloop=off;
CREATE TABLE IF NOT EXISTS public.cam_incident_20261024_source_reuse (
 new_source_id varchar(36) PRIMARY KEY,
 old_source_id varchar(36) NOT NULL UNIQUE,
 new_job_id varchar(36) NOT NULL UNIQUE,
 original_asset_id varchar(36),
 original_pipeline_id varchar(36),
 method varchar(30) NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 restored_at timestamptz,
 CONSTRAINT incident_method CHECK (method IN ('sha256+graph-etag','graph-item-etag'))
);
WITH old AS MATERIALIZED (
 SELECT sa.id,sa.external_source_id,
 sa.source_metadata->>'item_id' item_id,
 lower(sa.source_metadata->>'drive_id') drive_id,
 sa.source_metadata->>'drive_id' original_drive_id,
 sa.filename,sa.mime_type,sa.size_bytes,sa.provider_checksum,sa.provider_version,
 sa.source_created_at,sa.source_modified_at
 FROM source_assets sa
 WHERE sa.tenant_id='creative-assets-prod'
 AND sa.external_source_id='6678db98-bc47-410a-a6ca-8f3647d8db9d'
 AND sa.deleted_at='2026-10-24 01:38:25.357732+07'
), current AS MATERIALIZED (
 SELECT n.id source_id, j.id job_id,
 n.external_source_id,
 n.source_metadata->>'item_id' item_id,
 lower(n.source_metadata->>'drive_id') drive_id,
 n.source_metadata->>'drive_id' original_drive_id,
 n.filename,n.mime_type,n.size_bytes,n.provider_checksum,n.provider_version,
 n.source_created_at,n.source_modified_at
 FROM processing_jobs j
 JOIN source_assets n ON n.id=j.entity_id AND n.tenant_id=j.tenant_id
 JOIN external_sources es ON es.id=n.external_source_id AND es.tenant_id=n.tenant_id
 JOIN oauth_connections oc ON oc.id=es.oauth_connection_id AND oc.tenant_id=es.tenant_id
 WHERE j.tenant_id='creative-assets-prod'
 AND j.job_type='source_asset_download' AND j.entity_type='source_asset'
 AND j.status='pending' AND j.cancellation_requested IS FALSE
 AND j.attempt_count<j.max_attempts
 AND j.idempotency_key='source-asset-download:'||n.id||':initial-import-v2'
 AND n.deleted_at IS NULL AND n.source_metadata->>'item_id' IS NOT NULL
 AND es.source_type='onedrive' AND es.status='active'
 AND oc.status='active' AND oc.revoked_at IS NULL
 AND n.external_source_id='6678db98-bc47-410a-a6ca-8f3647d8db9d'
)
INSERT INTO cam_incident_20261024_source_reuse
(new_source_id,old_source_id,new_job_id,method)
SELECT n.source_id,o.id,n.job_id,
CASE WHEN n.provider_checksum IS NOT NULL
THEN 'sha256+graph-etag' ELSE 'graph-item-etag' END
FROM current n
JOIN old o ON o.item_id=n.item_id AND o.external_source_id=n.external_source_id
 AND o.drive_id=n.drive_id AND o.original_drive_id<>n.original_drive_id
 AND o.filename=n.filename AND o.mime_type=n.mime_type
 AND o.size_bytes=n.size_bytes AND o.provider_version=n.provider_version
 AND o.source_created_at IS NOT DISTINCT FROM n.source_created_at
 AND o.source_modified_at IS NOT DISTINCT FROM n.source_modified_at
WHERE (n.provider_checksum IS NULL AND o.provider_checksum IS NULL)
   OR (n.provider_checksum ~ '^[0-9a-fA-F]{64}$'
       AND lower(n.provider_checksum)=lower(o.provider_checksum))
ON CONFLICT DO NOTHING;
COMMIT;
SELECT method,count(*) mapped FROM cam_incident_20261024_source_reuse GROUP BY method ORDER BY method;
