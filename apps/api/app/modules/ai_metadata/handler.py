from __future__ import annotations

import asyncio

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    JobHandlerContext,
    JobHandlerResult,
)
from app.modules.ai_metadata.service import AiAnalysisService


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
        if context.dependencies.storage_provider is None:
            return JobHandlerResult.non_retryable(
                "storage_provider_unconfigured",
                "Asset storage provider is not configured.",
            )
        if context.dependencies.ai_provider is None:
            return JobHandlerResult.non_retryable(
                "ai_provider_unconfigured",
                "AI metadata provider is not configured.",
            )
        service = AiAnalysisService(
            session_factory=context.dependencies.session_factory,
            storage_provider=context.dependencies.storage_provider,
            ai_provider=context.dependencies.ai_provider,
            settings=settings,
        )
        outcome = asyncio.run(
            service.analyze(
                tenant_id=context.job.tenant_id,
                analysis_id=analysis_id,
                worker_id=context.job.lease_owner,
                is_cancelled=lambda: context.is_cancelled,
            )
        )
        if outcome.status == "completed":
            return JobHandlerResult.completed()
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
