from __future__ import annotations

import asyncio
import inspect

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.model import SourceSyncRunModel
from app.modules.source_sync.repository import SourceSyncRepository
from app.modules.source_sync.service import SourceSyncService
from app.providers.google.auth import get_connection_access_token


class SourceSyncJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not (
            settings.PROCESSING_JOBS_ENABLED
            and settings.UNIFIED_ASSET_INGESTION_ENABLED
            and settings.INCREMENTAL_SOURCE_SYNC_ENABLED
        ):
            return JobHandlerResult.non_retryable(
                "source_sync_disabled",
                "Google login source synchronization is disabled.",
            )
        source_id = context.job.payload.get("external_source_id")
        connection_id = context.job.payload.get("oauth_connection_id")
        if not isinstance(source_id, str) or not source_id:
            return JobHandlerResult.non_retryable(
                "invalid_source_sync_payload", "External source ID is required."
            )
        if not isinstance(connection_id, str) or not connection_id:
            return JobHandlerResult.non_retryable(
                "invalid_source_sync_payload", "OAuth connection ID is required."
            )
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        if context.dependencies.source_provider_factory is None:
            return JobHandlerResult.non_retryable(
                "source_provider_unconfigured", "Source provider is unavailable."
            )

        with context.dependencies.session_factory() as session:
            repository = SourceSyncRepository(session)
            source = repository.get_source(context.job.tenant_id, source_id)
            if source is None:
                return JobHandlerResult.non_retryable(
                    "source_not_found", "External source is unavailable."
                )
            if (
                source.source_type != "google_drive"
                or source.source_metadata.get("oauth_connection_id") != connection_id
            ):
                return JobHandlerResult.non_retryable(
                    "source_connection_mismatch",
                    "External source does not match the OAuth connection.",
                )
            completed_full_sync = session.scalar(
                select(SourceSyncRunModel.id).where(
                    SourceSyncRunModel.tenant_id == context.job.tenant_id,
                    SourceSyncRunModel.external_source_id == source_id,
                    SourceSyncRunModel.mode == "full",
                    SourceSyncRunModel.status == "completed",
                ).limit(1)
            )

        reconciliation = bool(
            context.job.payload.get(
                "reconciliation",
                settings.GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED
                and completed_full_sync is None,
            )
        )
        resolver = context.dependencies.resources.get(
            "google_connection_access_token_resolver",
            get_connection_access_token,
        )

        async def run_sync() -> None:
            token = resolver(connection_id)
            if inspect.isawaitable(token):
                token = await token
            async with context.dependencies.source_provider_factory(
                "google-drive", token
            ) as provider:
                with context.dependencies.session_factory() as sync_session:
                    await SourceSyncService(
                        SourceSyncRepository(sync_session),
                        ProcessingRepository(sync_session),
                        enabled=True,
                        settings=settings,
                    ).sync_source(
                        tenant_id=context.job.tenant_id,
                        source_id=source_id,
                        provider=provider,
                        reconciliation=reconciliation,
                        continue_check=lambda: not context.is_cancelled
                        and not context.shutdown_requested.is_set(),
                    )

        try:
            executor = context.dependencies.resources.get("async_executor")
            if executor is not None:
                executor.run(run_sync())
            else:
                asyncio.run(run_sync())
            return JobHandlerResult.completed()
        except Exception as exc:
            if context.is_cancelled or context.shutdown_requested.is_set():
                return JobHandlerResult.cancelled()
            return JobHandlerResult.retryable(type(exc).__name__, str(exc))
