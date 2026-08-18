from __future__ import annotations
import asyncio
from sqlalchemy import select
from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.assets.model import SourceAssetModel
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
from app.modules.video_search.indexing import VideoIndexDataError, build_video_document
from app.modules.video_search.model import VideoAnalysisChunkModel, VideoAnalysisRunModel

class VideoSearchIndexJobHandler:
    def __init__(self, settings: Settings|None=None): self.settings=settings
    def __call__(self, context: JobHandlerContext):
        settings=self.settings or get_settings()
        run_id=context.job.payload.get("analysis_run_id")
        if not isinstance(run_id,str) or not run_id: return JobHandlerResult.non_retryable("invalid_video_index_job","analysis_run_id is required")
        if context.cancellation_requested.is_set() or context.shutdown_requested.is_set(): return JobHandlerResult.cancelled()
        with context.dependencies.session_factory() as session:
            run=session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.tenant_id==context.job.tenant_id,VideoAnalysisRunModel.id==run_id))
            source=None if run is None else session.scalar(select(SourceAssetModel).where(SourceAssetModel.tenant_id==context.job.tenant_id,SourceAssetModel.id==run.source_asset_id))
            chunks=[] if run is None else list(session.scalars(select(VideoAnalysisChunkModel).where(VideoAnalysisChunkModel.tenant_id==context.job.tenant_id,VideoAnalysisChunkModel.run_id==run.id)))
            if run is None or source is None: return JobHandlerResult.non_retryable("video_index_source_unavailable","completed video source is unavailable")
            try: document=build_video_document(run=run,source=source,chunks=chunks)
            except VideoIndexDataError as exc: return JobHandlerResult.non_retryable("invalid_video_index_data",str(exc))
        try:
            index=VideoSearchElasticsearchIndex(ElasticsearchV3Config(settings.ELASTICSEARCH_URL,index_prefix=settings.ELASTICSEARCH_INDEX_PREFIX,index_generation="v3"))
            operation=index.upsert_video_document(document)
            executor=context.dependencies.resources.get("async_executor")
            if executor: executor.run(operation)
            else: asyncio.run(operation)
            return JobHandlerResult.completed()
        except ElasticsearchV3RequestError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
                return JobHandlerResult.non_retryable("video_index_elasticsearch_request_rejected",str(exc))
            return JobHandlerResult.retryable("video_index_elasticsearch_unavailable",str(exc))
