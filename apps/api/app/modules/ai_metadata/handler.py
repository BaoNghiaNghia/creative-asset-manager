from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.ai_metadata.service import AiAnalysisService
from app.modules.ai_metadata.source_storage import PipelineSourceAssetStorage
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import PipelineState
from app.modules.processing.repository import ProcessingRepository
from app.modules.storage.repository import ManagedStorageRepository


_STORAGE_WAIT_SECONDS = 30
_STORAGE_IN_PROGRESS = frozenset({"pending", "uploading", "retry"})
_PIPELINE_STORAGE_IN_PROGRESS = frozenset({
    PipelineState.DOWNLOADED.value,
    PipelineState.DUPLICATE_DETECTED.value,
    PipelineState.STORAGE_PENDING.value,
})


class AssetAnalyzeJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        analysis_id = context.job.payload.get("analysis_id") or context.job.entity_id
        if not isinstance(analysis_id, str) or not analysis_id:
            return JobHandlerResult.non_retryable(
                "invalid_analysis_job",
                "asset_analyze job requires an analysis_id.",
            )
        settings = self.settings or get_settings()
        pipeline_id = context.job.payload.get("pipeline_id")
        source_fallback = (
            settings.AI_ANALYSIS_SOURCE_FALLBACK_ENABLED
            and context.job.payload.get("analysis_content_source") == "source_asset"
            and isinstance(pipeline_id, str)
            and bool(pipeline_id)
        )
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
            asset_id = analysis.asset_id
        storage_gate = self._managed_storage_gate(
            context,
            settings,
            asset_id=asset_id,
            source_fallback=source_fallback,
        )
        if storage_gate is not None:
            return storage_gate
        try:
            provider = registry.require(provider_name or "")
        except (AiProviderUnavailableError, ValueError):
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "The persisted analysis AI provider is not configured.",
            )
        if isinstance(pipeline_id, str) and pipeline_id:
            with context.dependencies.session_factory() as session:
                pipelines = AssetPipelineRepository(session)
                pipeline = pipelines.get(context.job.tenant_id, pipeline_id, for_update=True)
                if pipeline and pipeline.state == PipelineState.ANALYSIS_PENDING.value:
                    pipelines.transition(pipeline, PipelineState.ANALYZING)
                    session.commit()
        storage_provider = context.dependencies.storage_provider
        if source_fallback:
            resolver = context.dependencies.resources.get("pipeline_content_resolver")
            if resolver is None:
                return JobHandlerResult.non_retryable(
                    "source_analysis_resolver_unconfigured",
                    "Source analysis resolver is not configured.",
                )
            with context.dependencies.session_factory() as session:
                pipeline = AssetPipelineRepository(session).get(
                    context.job.tenant_id, pipeline_id
                )
                if (
                    pipeline is None
                    or pipeline.asset_id != analysis.asset_id
                    or not pipeline.source_asset_id
                ):
                    return JobHandlerResult.non_retryable(
                        "source_analysis_identity_mismatch",
                        "Source analysis identity could not be verified.",
                    )
                storage_provider = PipelineSourceAssetStorage(
                    resolver,
                    tenant_id=context.job.tenant_id,
                    pipeline=pipeline,
                )
        if storage_provider is None:
            return JobHandlerResult.non_retryable(
                "storage_provider_unconfigured",
                "Asset storage provider is not configured.",
            )
        service = AiAnalysisService(
            session_factory=context.dependencies.session_factory,
            storage_provider=storage_provider,
            ai_provider=provider,
            settings=settings,
            allow_unstored_source=source_fallback,
        )
        outcome = asyncio.run(
            service.analyze(
                tenant_id=context.job.tenant_id,
                analysis_id=analysis_id,
                worker_id=context.job.lease_owner,
                is_cancelled=lambda: context.is_cancelled,
                job_id=context.job.id,
                pilot_run_id=context.job.payload.get("pilot_run_id"),
                pipeline_id=pipeline_id if isinstance(pipeline_id, str) else None,
                enqueue_index=not (isinstance(pipeline_id, str) and bool(pipeline_id)),
            )
        )
        if outcome.status == "deferred":
            if outcome.retry_at is None:
                return JobHandlerResult.retryable(
                    "invalid_deferred_analysis_outcome",
                    "Deferred analysis did not provide a retry timestamp.",
                )
            return DeferredJobOutcome(
                reason_code=outcome.error_code or "gemini_quota_deferred",
                reason_message=outcome.error_message or "Gemini capacity is temporarily unavailable.",
                retry_at=outcome.retry_at,
                metadata=outcome.metadata,
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

    @staticmethod
    def _managed_storage_gate(
        context: JobHandlerContext,
        settings: Settings,
        *,
        asset_id: str,
        source_fallback: bool,
    ) -> JobHandlerResult | DeferredJobOutcome | None:
        if not settings.MANAGED_ASSET_STORAGE_ENABLED or source_fallback:
            return None
        now = datetime.now(timezone.utc)
        with context.dependencies.session_factory() as session:
            storage = ManagedStorageRepository(session).get(
                context.job.tenant_id, asset_id, "google_drive_managed"
            )
            if storage is not None and storage.status == "stored" and storage.remote_file_id:
                return None
            pipeline = session.scalar(
                select(AssetPipelineModel)
                .where(
                    AssetPipelineModel.tenant_id == context.job.tenant_id,
                    AssetPipelineModel.asset_id == asset_id,
                )
                .order_by(AssetPipelineModel.updated_at.desc(), AssetPipelineModel.id.desc())
                .limit(1)
            )
            storage_waiting = storage is not None and storage.status in _STORAGE_IN_PROGRESS
            pipeline_waiting = (
                pipeline is not None
                and pipeline.state in _PIPELINE_STORAGE_IN_PROGRESS
            )
            if storage_waiting or pipeline_waiting:
                retry_at = storage.next_attempt_at if storage is not None else None
                if retry_at is not None and retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if retry_at is None or retry_at <= now:
                    retry_at = now + timedelta(seconds=_STORAGE_WAIT_SECONDS)
                return DeferredJobOutcome(
                    reason_code="managed_asset_storage_pending",
                    reason_message="Managed storage is still preparing this asset.",
                    retry_at=retry_at,
                    metadata={"asset_id": asset_id},
                )
            if (
                storage is not None and storage.status == "failed"
            ) or (
                pipeline is not None
                and pipeline.state == PipelineState.STORAGE_FAILED.value
            ):
                return JobHandlerResult.non_retryable(
                    "managed_asset_storage_failed",
                    "Managed storage failed before AI analysis could start.",
                )
            return JobHandlerResult.non_retryable(
                "managed_asset_storage_missing",
                "No managed storage object is available for this asset.",
            )
