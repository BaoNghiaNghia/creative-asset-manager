from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.storage.managed_cleanup import ManagedStorageCleanupService
from app.modules.storage.managed_cleanup_scheduler import ManagedStorageCleanupScheduler


class ManagedStorageCleanupJobHandler:
    def __init__(self, settings: Settings):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        if not self.settings.MANAGED_STORAGE_AUTO_CLEANUP_ENABLED:
            return JobHandlerResult.non_retryable(
                "managed_storage_cleanup_disabled", "Managed storage cleanup is disabled."
            )
        try:
            result = asyncio.run(ManagedStorageCleanupService(
                context.dependencies.session_factory,
                self.settings,
                context.dependencies.storage_provider,
            ).execute(
                tenant_id=context.job.tenant_id,
                limit=self.settings.MANAGED_STORAGE_CLEANUP_MAX_ITEMS_PER_RUN,
            ))
            context.logger.info(
                "managed_storage_cleanup_completed",
                extra={"tenant_id": context.job.tenant_id, "counts": result.document()},
            )
            next_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.settings.MANAGED_STORAGE_CLEANUP_INTERVAL_SECONDS
            )
            ManagedStorageCleanupScheduler(
                context.dependencies.session_factory, self.settings
            ).schedule_tenant(context.job.tenant_id, next_attempt_at=next_at)
            return JobHandlerResult.completed()
        except Exception as exc:
            return JobHandlerResult.retryable(
                type(exc).__name__, "Managed storage cleanup failed; sensitive values were not logged."
            )
