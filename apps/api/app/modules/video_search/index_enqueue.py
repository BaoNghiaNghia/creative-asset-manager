from __future__ import annotations
import hashlib
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.model import VideoAnalysisRunModel

VIDEO_SEARCH_INDEX_PRIORITY = 10

def enqueue_video_search_index_job(*, tenant_id: str, run: VideoAnalysisRunModel, processing: ProcessingRepository) -> bool:
    if run.tenant_id != tenant_id or run.status != "completed": return False
    key="video-search-index:"+hashlib.sha256(f"{tenant_id}:{run.id}".encode()).hexdigest()
    before=processing.count_jobs()
    processing.create_job(tenant_id=tenant_id,job_type="video_search_index",entity_type="video_analysis_run",entity_id=run.id,idempotency_key=key,payload={"analysis_run_id":run.id},priority=VIDEO_SEARCH_INDEX_PRIORITY,provider_key="elasticsearch",provider_scope="video")
    return processing.count_jobs()>before
