BEGIN;
SET LOCAL statement_timeout='60s';
SET LOCAL lock_timeout='4s';
SET LOCAL enable_nestloop=off;

ALTER TABLE cam_incident_20261024_source_reuse
  ADD COLUMN IF NOT EXISTS original_job_due timestamptz;
ALTER TABLE cam_incident_20261024_source_reuse
  ADD COLUMN IF NOT EXISTS original_job_priority integer;
ALTER TABLE cam_incident_20261024_source_reuse
  ADD COLUMN IF NOT EXISTS restored_pipeline_id varchar(36);

WITH usable AS MATERIALIZED (
 SELECT m.new_source_id,l.asset_id,p.id old_pipeline_id
 FROM cam_incident_20261024_source_reuse m
 JOIN source_assets new_sa ON new_sa.tenant_id='creative-assets-prod'
                             AND new_sa.id=m.new_source_id
 JOIN source_assets old_sa ON old_sa.tenant_id='creative-assets-prod'
                             AND old_sa.id=m.old_source_id
 JOIN asset_source_links l ON l.tenant_id='creative-assets-prod'
                           AND l.source_asset_id=m.old_source_id
 JOIN assets a ON a.tenant_id='creative-assets-prod' AND a.id=l.asset_id
 JOIN asset_pipelines p ON p.tenant_id='creative-assets-prod'
                       AND p.origin_type='source_asset' AND p.origin_id=m.old_source_id
                       AND p.asset_id=a.id
 JOIN asset_ai_analyses aa ON aa.tenant_id='creative-assets-prod'
                          AND aa.id=p.analysis_id AND aa.asset_id=a.id
 WHERE m.restored_at IS NULL
 AND p.state='completed' AND aa.status='completed'
 AND p.content_hash=a.content_hash
 AND p.indexed_projection_version IS NOT NULL
 AND p.indexed_projection_checksum IS NOT NULL
 AND a.size_bytes=new_sa.size_bytes AND a.mime_type=new_sa.mime_type
 AND new_sa.deleted_at IS NULL
 AND new_sa.provider_version=old_sa.provider_version
 AND ( (m.method='sha256+graph-etag'
         AND LOWER(new_sa.provider_checksum)=a.content_hash)
       OR (m.method='graph-item-etag'
         AND new_sa.provider_checksum IS NULL
         AND old_sa.provider_checksum IS NULL) )
), marked AS (
 UPDATE cam_incident_20261024_source_reuse m
 SET original_asset_id=u.asset_id,
     original_pipeline_id=u.old_pipeline_id
 FROM usable u WHERE m.new_source_id=u.new_source_id
 RETURNING m.method
)
SELECT method,count(*) recoverable FROM marked GROUP BY 1 ORDER BY 1;
COMMIT;
SELECT method,
 COUNT(*) mapped,
 COUNT(*) FILTER(WHERE original_pipeline_id IS NOT NULL) recoverable
 FROM cam_incident_20261024_source_reuse GROUP BY method ORDER BY method;
