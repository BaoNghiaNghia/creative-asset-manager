from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.projection_service import SearchProjectionService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import PipelineState
from app.modules.processing.repository import ProcessingRepository
from app.modules.search.index_types import SearchIndexDocument, SearchIndexProvider
from app.modules.storage.sidecar_document import MetadataSidecarDocumentBuilder
from app.modules.storage.sidecar_repository import MetadataSidecarRepository
from app.modules.storage.sidecar_service import MetadataSidecarExportService


@dataclass(frozen=True, slots=True)
class DownloadStageResult:
    source_asset_id: str
    asset_id: str
    content_hash: str
    duplicate: bool


class PipelineDownloadStage(Protocol):
    async def execute(self, *, tenant_id: str, pipeline: AssetPipelineModel) -> DownloadStageResult: ...


class PipelineStorageStage(Protocol):
    async def execute(self, *, tenant_id: str, pipeline: AssetPipelineModel) -> None: ...


class _PipelineHandler:
    failure_stage = ""


    def _load(self, context: JobHandlerContext):
        pipeline_id = context.job.payload.get("pipeline_id")
        session = context.dependencies.session_factory()
        repository = AssetPipelineRepository(session)
        if not isinstance(pipeline_id, str) or not pipeline_id:
            origin_type = (
                "ingestion_item"
                if context.job.entity_type in {"asset_ingestion_item", "ingestion_item"}
                else "source_asset"
            )
            source_asset_id = context.job.entity_id if origin_type == "source_asset" else None
            pipeline = repository.get_or_create(
                tenant_id=context.job.tenant_id, origin_type=origin_type,
                origin_id=context.job.entity_id, source_asset_id=source_asset_id,
                correlation_id=f"{origin_type}:{context.job.entity_id}",
            )
            if pipeline.state == PipelineState.DISCOVERED.value:
                repository.transition(pipeline, PipelineState.DOWNLOAD_PENDING)
            session.flush()
            return session, repository, pipeline
        pipeline = repository.get(context.job.tenant_id, pipeline_id, for_update=True)
        if pipeline is None:
            session.close()
            raise LookupError(pipeline_id)
        return session, repository, pipeline

    def _failed(self, context: JobHandlerContext, exc: Exception, *, retryable: bool = True) -> JobHandlerResult:
        try:
            session, repository, pipeline = self._load(context)
            try:
                expected = f"{self.failure_stage}_failed"
                if pipeline.state != expected:
                    repository.record_failure(
                        pipeline, self.failure_stage, error_code=type(exc).__name__,
                        error_message=str(exc), retryable=retryable,
                    )
                session.commit()
            finally:
                session.close()
        except Exception:
            pass
        if retryable:
            return JobHandlerResult.retryable(type(exc).__name__, str(exc))
        return JobHandlerResult.non_retryable(type(exc).__name__, str(exc))


class SourceAssetDownloadJobHandler(_PipelineHandler):
    failure_stage = "download"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not (settings.UNIFIED_ASSET_INGESTION_ENABLED and settings.CONTENT_DEDUP_ENABLED):
            return JobHandlerResult.non_retryable("asset_pipeline_disabled", "Unified ingestion and content deduplication must be enabled.")
        stage = context.dependencies.resources.get("pipeline_download_stage")
        if stage is None:
            return JobHandlerResult.non_retryable("download_stage_unconfigured", "Pipeline download stage is not configured.")
        try:
            session, repository, pipeline = self._load(context)
            try:
                if pipeline.state not in {PipelineState.DOWNLOAD_PENDING.value, PipelineState.DOWNLOAD_FAILED.value}:
                    return JobHandlerResult.completed()
                if pipeline.state == PipelineState.DOWNLOAD_FAILED.value:
                    repository.transition(pipeline, PipelineState.DOWNLOAD_PENDING)
                repository.transition(pipeline, PipelineState.DOWNLOADING)
                session.commit()
            finally:
                session.close()
            result = asyncio.run(stage.execute(tenant_id=context.job.tenant_id, pipeline=pipeline))
            session, repository, pipeline = self._load(context)
            try:
                pipeline.source_asset_id = result.source_asset_id
                pipeline.asset_id = result.asset_id
                pipeline.content_hash = result.content_hash
                repository.transition(pipeline, PipelineState.DUPLICATE_DETECTED if result.duplicate else PipelineState.DOWNLOADED)
                coordinator = AssetPipelineService(repository, ProcessingRepository(session))
                if settings.MANAGED_ASSET_STORAGE_ENABLED:
                    coordinator.enqueue(pipeline, "asset_store")
                else:
                    self._enqueue_after_storage(coordinator, pipeline, settings)
                session.commit()
            finally:
                session.close()
            return JobHandlerResult.completed()
        except Exception as exc:
            return self._failed(context, exc)


    @staticmethod
    def _enqueue_after_storage(coordinator: AssetPipelineService, pipeline: AssetPipelineModel, settings: Settings) -> None:
        session = coordinator.pipelines.session
        completed = session.scalar(select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id == pipeline.tenant_id,
            AssetAiAnalysisModel.asset_id == pipeline.asset_id,
            AssetAiAnalysisModel.status == "completed",
        ).order_by(AssetAiAnalysisModel.completed_at.desc()).limit(1))
        if completed is not None:
            pipeline.analysis_id = completed.id
            coordinator.enqueue(pipeline, "search_projection_build")
            return
        if (settings.MANAGED_ASSET_STORAGE_ENABLED and settings.DYNAMIC_AI_METADATA_ENABLED and settings.AI_AUTO_ANALYZE_ENABLED
                and settings.AI_SINGLE_ANALYSIS_ENABLED):
            profile = session.scalar(select(MetadataProfileModel).where(
                MetadataProfileModel.tenant_id == pipeline.tenant_id,
                MetadataProfileModel.active.is_(True),
            ).order_by(MetadataProfileModel.created_at.desc()).limit(1))
            if profile is not None:
                analysis = AiMetadataRepository(session).create_analysis(
                    tenant_id=pipeline.tenant_id, asset_id=pipeline.asset_id,
                    metadata_profile_id=profile.id, prompt_version="auto-v1",
                    pipeline_version="asset-pipeline-v1", ai_provider="gemini",
                )
                pipeline.analysis_id = analysis.id
                coordinator.enqueue(pipeline, "asset_analyze", payload={"analysis_id": analysis.id})
                return
        coordinator.pipelines.transition(pipeline, PipelineState.COMPLETED)


class AssetStoreJobHandler(_PipelineHandler):
    failure_stage = "storage"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not settings.MANAGED_ASSET_STORAGE_ENABLED:
            return JobHandlerResult.non_retryable("managed_storage_disabled", "Managed storage is disabled.")
        stage = context.dependencies.resources.get("pipeline_storage_stage")
        if stage is None:
            return JobHandlerResult.non_retryable("storage_stage_unconfigured", "Pipeline storage stage is not configured.")
        try:
            session, repository, pipeline = self._load(context)
            if pipeline.state not in {PipelineState.STORAGE_PENDING.value, PipelineState.STORAGE_FAILED.value}:
                session.close()
                return JobHandlerResult.completed()

            session.close()
            asyncio.run(stage.execute(tenant_id=context.job.tenant_id, pipeline=pipeline))
            session, repository, pipeline = self._load(context)
            try:
                if pipeline.state == PipelineState.STORAGE_FAILED.value:
                    repository.transition(pipeline, PipelineState.STORAGE_PENDING)
                if pipeline.state == PipelineState.STORAGE_PENDING.value:
                    repository.transition(pipeline, PipelineState.STORED)
                coordinator = AssetPipelineService(repository, ProcessingRepository(session))
                SourceAssetDownloadJobHandler._enqueue_after_storage(coordinator, pipeline, settings)
                session.commit()
            finally:
                session.close()
            return JobHandlerResult.completed()
        except Exception as exc:
            return self._failed(context, exc)


class SearchProjectionBuildJobHandler(_PipelineHandler):
    failure_stage = "projection"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not settings.SEARCH_PROJECTION_ENABLED:
            return JobHandlerResult.non_retryable("search_projection_disabled", "Search projection is disabled.")
        try:
            session, repository, pipeline = self._load(context)
            if pipeline.state not in {PipelineState.PROJECTION_PENDING.value, PipelineState.PROJECTION_FAILED.value}:
                session.close()
                return JobHandlerResult.completed()

            try:
                analysis = self._analysis(session, pipeline)
                if analysis.search_projection is None:
                    SearchProjectionService(AiMetadataRepository(session), SearchProjectionBuilder(), enabled=True).rebuild(analysis.id)
                    session.refresh(analysis)
                if not isinstance(analysis.search_projection, Mapping) or not analysis.search_projection_version:
                    raise ValueError("analysis has no valid search projection")
                encoded = json.dumps(analysis.search_projection, sort_keys=True, separators=(",", ":")).encode()
                checksum = analysis.projection_checksum or hashlib.sha256(encoded).hexdigest()
                analysis.projection_checksum = checksum
                pipeline.analysis_id = analysis.id
                pipeline.projection_version = analysis.search_projection_version
                pipeline.projection_checksum = checksum
                if pipeline.state == PipelineState.PROJECTION_FAILED.value:
                    repository.transition(pipeline, PipelineState.PROJECTION_PENDING)
                repository.transition(pipeline, PipelineState.PROJECTION_READY)
                AssetPipelineService(repository, ProcessingRepository(session)).enqueue(pipeline, "asset_index")
                session.commit()
            finally:
                session.close()
            return JobHandlerResult.completed()
        except Exception as exc:
            return self._failed(context, exc)

    @staticmethod
    def _analysis(session, pipeline: AssetPipelineModel) -> AssetAiAnalysisModel:
        if pipeline.analysis_id:
            analysis = session.get(AssetAiAnalysisModel, pipeline.analysis_id)
        else:
            analysis = session.scalar(select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == pipeline.tenant_id,
                AssetAiAnalysisModel.asset_id == pipeline.asset_id,
                AssetAiAnalysisModel.status == "completed",
            ).order_by(AssetAiAnalysisModel.completed_at.desc()).limit(1))
        if analysis is None or analysis.tenant_id != pipeline.tenant_id or analysis.status != "completed":
            raise ValueError("no completed analysis is available")
        return analysis


class AssetIndexJobHandler(_PipelineHandler):
    failure_stage = "search"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not settings.ELASTICSEARCH_V2_ENABLED:
            return JobHandlerResult.non_retryable("elasticsearch_v2_disabled", "Elasticsearch v2 is disabled.")
        provider = context.dependencies.resources.get("search_index_provider")
        if provider is None:
            return JobHandlerResult.non_retryable("search_index_unconfigured", "Search index provider is not configured.")
        try:
            session, repository, pipeline = self._load(context)
            if pipeline.state == PipelineState.COMPLETED.value:
                session.close()
                return JobHandlerResult.completed()

            try:
                analysis = SearchProjectionBuildJobHandler._analysis(session, pipeline)
                unchanged = (
                    pipeline.indexed_projection_version == analysis.search_projection_version
                    and pipeline.indexed_projection_checksum == analysis.projection_checksum
                )
                document = None if unchanged else self._document(session, analysis)
            finally:
                session.close()
            if document is not None:
                asyncio.run(provider.bulk_upsert((document,)))
            session, repository, pipeline = self._load(context)
            try:
                if pipeline.state == PipelineState.SEARCH_FAILED.value:
                    repository.transition(pipeline, PipelineState.SEARCH_PENDING)
                repository.transition(pipeline, PipelineState.INDEXED)
                pipeline.indexed_projection_version = analysis.search_projection_version
                pipeline.indexed_projection_checksum = analysis.projection_checksum
                coordinator = AssetPipelineService(repository, ProcessingRepository(session))
                if settings.DRIVE_METADATA_SIDECAR_ENABLED:
                    coordinator.enqueue(pipeline, "metadata_sidecar_export", payload={"analysis_id": analysis.id})
                else:
                    repository.transition(pipeline, PipelineState.COMPLETED)
                session.commit()
            finally:
                session.close()
            return JobHandlerResult.completed()
        except Exception as exc:
            return self._failed(context, exc)

    @staticmethod
    def _document(session, analysis: AssetAiAnalysisModel) -> SearchIndexDocument:
        projection = analysis.search_projection
        if not isinstance(projection, Mapping):
            raise ValueError("analysis has no persisted projection")
        source = session.scalar(select(SourceAssetModel).join(
            AssetSourceLinkModel, AssetSourceLinkModel.source_asset_id == SourceAssetModel.id
        ).where(AssetSourceLinkModel.asset_id == analysis.asset_id).order_by(SourceAssetModel.created_at).limit(1))
        metadata = source.source_metadata if source else {}
        facets = projection.get("facets") or {}
        return SearchIndexDocument(
            asset_id=analysis.asset_id, tenant_id=analysis.tenant_id,
            filename=source.filename if source and source.filename else "",
            folder_path=str(metadata.get("path") or metadata.get("folder_path") or ""),
            search_text=str(projection.get("search_text") or ""),
            search_terms=tuple(projection.get("search_terms") or ()),
            normalized_terms=tuple(projection.get("normalized_terms") or ()),
            phrases=tuple(projection.get("phrases") or ()),
            numbers=tuple(projection.get("numbers") or ()),
            facets={key: tuple(value) for key, value in facets.items()},
            path_values=tuple(projection.get("path_values") or ()),
            metadata_profile=analysis.metadata_profile,
            metadata_profile_version=analysis.metadata_profile_version,
            search_projection_version=analysis.search_projection_version or "",
        )


class MetadataSidecarExportJobHandler(_PipelineHandler):
    failure_stage = "sidecar"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not settings.DRIVE_METADATA_SIDECAR_ENABLED:
            return JobHandlerResult.non_retryable("sidecar_disabled", "Metadata sidecar export is disabled.")
        if context.dependencies.storage_provider is None:
            return JobHandlerResult.non_retryable("storage_provider_unconfigured", "Storage provider is not configured.")
        try:
            session, repository, pipeline = self._load(context)
            if pipeline.state == PipelineState.COMPLETED.value:
                session.close()
                return JobHandlerResult.completed()

            analysis_id = pipeline.analysis_id
            session.close()
            if not analysis_id:
                raise ValueError("pipeline has no analysis")
            with context.dependencies.session_factory() as sidecar_session:
                asyncio.run(MetadataSidecarExportService(
                    MetadataSidecarRepository(sidecar_session), MetadataSidecarDocumentBuilder(sidecar_session), enabled=True,
                ).export(tenant_id=context.job.tenant_id, analysis_id=analysis_id, provider=context.dependencies.storage_provider))
            session, repository, pipeline = self._load(context)
            try:
                if pipeline.state == PipelineState.SIDECAR_FAILED.value:
                    repository.transition(pipeline, PipelineState.SIDECAR_PENDING)
                repository.transition(pipeline, PipelineState.COMPLETED)
                session.commit()
            finally:
                session.close()
            return JobHandlerResult.completed()
        except Exception as exc:
            return self._failed(context, exc)
