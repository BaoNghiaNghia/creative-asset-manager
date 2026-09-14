from sqlalchemy import select
from app.modules.processing.model import ProcessingJobModel,PROCESSING_JOB_RUNNING_STATUSES,PROCESSING_JOB_QUEUED_STATUSES
from app.modules.visual_search.lifecycle import visual_index_job_key
class VisualJobCoverageReader:
 def __init__(self,session):self.session=session
 def counts(self,tenant_id,assets,current_ids=set()):
  rows=self.session.scalars(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id==tenant_id,ProcessingJobModel.job_type=="visual_index_sync")).all();out={"visual_jobs_pending":0,"visual_jobs_processing":0,"visual_jobs_failed":0};seen=set()
  for j in rows:
   h=assets.get(j.entity_id)
   if not h or j.idempotency_key!=visual_index_job_key(j.entity_id,h) or j.entity_id in seen:continue
   seen.add(j.entity_id)
   if j.status in PROCESSING_JOB_QUEUED_STATUSES or j.status=="retry":out["visual_jobs_pending"]+=1
   elif j.status in PROCESSING_JOB_RUNNING_STATUSES:out["visual_jobs_processing"]+=1
   elif j.status=="failed" and j.entity_id not in current_ids:out["visual_jobs_failed"]+=1
  return out
