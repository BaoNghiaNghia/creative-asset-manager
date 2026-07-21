from __future__ import annotations

import asyncio

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.ai_metadata.service import AiAnalysisService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import PipelineState
from app.modules.processing.repository import ProcessingRepository


class AssetAnalyzeJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        analysis_id = context.job.payload.get("analysis_id") or context.job.entity_id
        if not isinstance(analysis_id, str) or not analysis_id:
            return JobHandlerResult.non_retryable(
                "invalid_analysis_job",
                "asset_analyze job requires an analysis_id.",
            )
        settings = self.settings or get_settings()
        registry = context.dependencies.ai_provider_registry
        if registry is None:
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "The analysis AI provider is not configured.",
            )
        with context.dependencies.session_factory() as session:
            try:
                analysis = AiMetadataRepository(session).get_analysis(analysis_id)
            except LookupError:
                return JobHandlerResult.non_retryable(
                    "analysis_not_found", "Analysis was not found."
                )
            if analysis.tenant_id != context.job.tenant_id:
                return JobHandlerResult.non_retryable(
                    "analysis_not_found", "Analysis was not found."
                )
            provider_name = analysis.ai_provider
        try:
            provider = registry.require(provider_name or "")
        except (AiProviderUnavailableError, ValueError):
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "The persisted analysis AI provider is not configured.",
            )
        pipeline_id = context.job.payload.get("pipeline_id")
        if isinstance(pipeline_id, str) and pipeline_id:
            with context.dependencies.session_factory() as session:
                pipelines = AssetPipelineRepository(session)
                pipeline = pipelines.get(context.job.tenant_id, pipeline_id, for_update=True)
                if pipeline and pipeline.state == PipelineState.ANALYSIS_PENDING.value:
                    pipelines.transition(pipeline, PipelineState.ANALYZING)
                    session.commit()
        if context.dependencies.storage_provider is None:
            return JobHandlerResult.non_retryable(
                "storage_provider_unconfigured",
                "Asset storage provider is not configured.",
            )
        service = AiAnalysisService(
            session_factory=context.dependencies.session_factory,
            storage_provider=context.dependencies.storage_provider,
            ai_provider=provider,
            settings=settings,
        )
        outcome = asyncio.run(
            service.analyze(
                tenant_id=context.job.tenant_id,
                analysis_id=analysis_id,
                worker_id=context.job.lease_owner,
                is_cancelled=lambda: context.is_cancelled,
                job_id=context.job.id,
                pilot_run_id=context.job.payload.get("pilot_run_id"),
                enqueue_index=not (isinstance(pipeline_id, str) and bool(pipeline_id)),
            )
        )
        if outcome.status == "completed":
            if isinstance(pipeline_id, str) and pipeline_id:
                with context.dependencies.session_factory() as session:
                    pipelines = AssetPipelineRepository(session)
                    pipeline = pipelines.get(context.job.tenant_id, pipeline_id, for_update=True)
                    if pipeline and pipeline.state in {PipelineState.ANALYZING.value, PipelineState.ANALYSIS_PENDING.value}:
                        if pipeline.state == PipelineState.ANALYSIS_PENDING.value:
                            pipelines.transition(pipeline, PipelineState.ANALYZING)
                        pipelines.transition(pipeline, PipelineState.METADATA_READY)
                        analysis = AiMetadataRepository(session).get_analysis(analysis_id)
                        pipeline.analysis_id = analysis.id
                        coordinator = AssetPipelineService(pipelines, ProcessingRepository(session))
                        if analysis.search_projection and analysis.search_projection_version:
                            pipeline.projection_version = analysis.search_projection_version
                            pipeline.projection_checksum = analysis.projection_checksum
                            pipelines.transition(pipeline, PipelineState.PROJECTION_READY)
                            coordinator.enqueue(pipeline, "asset_index")
                        else:
                            coordinator.enqueue(pipeline, "search_projection_build")
                        session.commit()
            return JobHandlerResult.completed()
        if outcome.status == "budget_blocked":
            return JobHandlerResult.retryable(
                outcome.error_code or "budget_blocked",
                outcome.error_message or "AI budget blocked this job.",
            )
        if outcome.status == "retryable_failure":
            return JobHandlerResult.retryable(
                outcome.error_code or "analysis_failed",
                outcome.error_message or "Asset analysis failed.",
            )
        if outcome.status == "cancelled":
            return JobHandlerResult.cancelled(
                outcome.error_message or "Asset analysis was cancelled."
            )
        return JobHandlerResult.non_retryable(
            outcome.error_code or "analysis_failed",
            outcome.error_message or "Asset analysis failed.",
        )
