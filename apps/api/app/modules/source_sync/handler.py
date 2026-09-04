from __future__ import annotations

import asyncio
import inspect

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.source_credentials import source_credential_contract
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.model import SourceSyncRunModel
from app.modules.source_sync.repository import SourceSyncRepository
from app.modules.source_sync.service import SourceSyncService


class SourceSyncJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not (settings.PROCESSING_JOBS_ENABLED and settings.UNIFIED_ASSET_INGESTION_ENABLED and settings.INCREMENTAL_SOURCE_SYNC_ENABLED):
            return JobHandlerResult.non_retryable("source_sync_disabled", "Source synchronization is disabled.")
        source_id = context.job.payload.get("external_source_id")
        if not isinstance(source_id, str) or not source_id:
            return JobHandlerResult.non_retryable("invalid_source_sync_payload", "External source ID is required.")
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        if context.dependencies.source_provider_factory is None:
            return JobHandlerResult.non_retryable("source_provider_unconfigured", "Source provider is unavailable.")

        with context.dependencies.session_factory() as session:
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == context.job.tenant_id,
                ExternalSourceModel.id == source_id,
            ))
            if source is None:
                return JobHandlerResult.non_retryable("source_not_found", "External source is unavailable.")
            if source.status == "disconnected":
                return JobHandlerResult.non_retryable("source_disconnected", "External source is disconnected.")
            if source.status != "active":
                return JobHandlerResult.non_retryable("source_reconnect_required", "External source requires reconnection.")
            try:
                contract = source_credential_contract(source.source_type)
            except ValueError:
                return JobHandlerResult.non_retryable("source_type_unsupported", "External source type is unsupported.")
            completed_full_sync = session.scalar(select(SourceSyncRunModel.id).where(
                SourceSyncRunModel.tenant_id == context.job.tenant_id,
                SourceSyncRunModel.external_source_id == source_id,
                SourceSyncRunModel.mode == "full",
                SourceSyncRunModel.status == "completed",
            ).limit(1))

        reconciliation = bool(context.job.payload.get(
            "reconciliation",
            settings.GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED and completed_full_sync is None,
        ))
        injected_resolver = context.dependencies.resources.get("source_access_resolver")

        async def run_sync() -> None:
            if injected_resolver is not None:
                access = injected_resolver(context.job.tenant_id, source_id)
                if inspect.isawaitable(access):
                    access = await access
            else:
                with context.dependencies.session_factory() as session:
                    access = await TenantSourceResolver(session).resolve(
                        tenant_id=context.job.tenant_id, external_source_id=source_id
                    )
            adapter_key = source_credential_contract(access.source_type).adapter_key
            async with context.dependencies.source_provider_factory(adapter_key, access.access_token) as provider:
                with context.dependencies.session_factory() as sync_session:
                    await SourceSyncService(
                        SourceSyncRepository(sync_session), ProcessingRepository(sync_session),
                        enabled=True, settings=settings,
                    ).sync_source(
                        tenant_id=context.job.tenant_id, source_id=source_id, provider=provider,
                        reconciliation=reconciliation,
                        continue_check=lambda: not context.is_cancelled and not context.shutdown_requested.is_set(),
                    )

        try:
            executor = context.dependencies.resources.get("async_executor")
            if executor is not None: executor.run(run_sync())
            else: asyncio.run(run_sync())
            return JobHandlerResult.completed()
        except ValueError as exc:
            return JobHandlerResult.non_retryable("source_provider_unavailable", str(exc))
        except Exception:
            if context.is_cancelled or context.shutdown_requested.is_set():
                return JobHandlerResult.cancelled()
            return JobHandlerResult.retryable("source_sync_failed", "Source synchronization failed.")
