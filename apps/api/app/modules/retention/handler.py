from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.retention.scheduler import RetentionCleanupScheduler
from app.modules.retention.service import RetentionCleanupService


class RetentionCleanupJobHandler:
    def __init__(self, settings: Settings):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        if not self.settings.RETENTION_CLEANUP_ENABLED:
            return JobHandlerResult.non_retryable(
                "retention_cleanup_disabled", "Retention cleanup is disabled."
            )
        run_id = context.job.payload.get("cleanup_run_id")
        if not isinstance(run_id, str) or not run_id:
            return JobHandlerResult.non_retryable(
                "invalid_cleanup_payload", "cleanup_run_id is required."
            )
        try:
            run = RetentionCleanupService(
                context.dependencies.session_factory, self.settings
            ).execute(
                tenant_id=context.job.tenant_id,
                run_id=run_id,
                cancelled=lambda: context.is_cancelled,
            )
            context.logger.info(
                "retention_cleanup_checkpoint",
                extra={
                    "tenant_id": context.job.tenant_id,
                    "cleanup_run_id": run.id,
                    "status": run.status,
                    "record_types": run.record_types_json,
                    "counts": run.counts_json,
                    "checkpoint_version": run.checkpoint_version,
                },
            )
            if run.status == "cancelled":
                return JobHandlerResult.cancelled("Retention cleanup was cancelled.")
            if run.status != "completed":
                return JobHandlerResult.retryable(
                    "retention_cleanup_incomplete",
                    "Retention cleanup reached the bounded row limit and will resume.",
                )
            next_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.settings.RETENTION_CLEANUP_INTERVAL_SECONDS
            )
            RetentionCleanupScheduler(
                context.dependencies.session_factory, self.settings
            ).schedule_tenant(
                context.job.tenant_id, cutoff_at=next_at, next_attempt_at=next_at
            )
            return JobHandlerResult.completed()
        except Exception as exc:
            RetentionCleanupService(
                context.dependencies.session_factory, self.settings
            ).record_failure(
                tenant_id=context.job.tenant_id,
                run_id=run_id,
                error_code=type(exc).__name__,
            )
            return JobHandlerResult.retryable(
                type(exc).__name__, "Retention cleanup failed; sensitive values were not logged."
            )
