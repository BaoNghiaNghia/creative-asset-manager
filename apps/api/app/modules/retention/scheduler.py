from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.application_logs.model import LogApplicationModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.retention.service import (
    RECORD_TYPES, CleanupAlreadyRunning, RetentionCleanupService,
)


class RetentionCleanupScheduler:
    """Schedules cleanup in the existing durable worker queue."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings

    def _record_types(self) -> tuple[str, ...]:
        configured = self.settings.RETENTION_CLEANUP_RECORD_TYPES.strip()
        if configured.lower() == "all":
            return RECORD_TYPES
        selected = tuple(item.strip() for item in configured.split(","))
        if not selected or any(not item for item in selected):
            raise ValueError("RETENTION_CLEANUP_RECORD_TYPES contains an empty record type")
        if len(set(selected)) != len(selected) or any(item not in RECORD_TYPES for item in selected):
            raise ValueError("RETENTION_CLEANUP_RECORD_TYPES must contain unique supported record types")
        return selected

    def schedule_tenant(
        self, tenant_id: str, *, cutoff_at: datetime, next_attempt_at: datetime
    ) -> bool:
        record_types = self._record_types()
        try:
            run = RetentionCleanupService(
                self.session_factory, self.settings
            ).create_run(tenant_id=tenant_id, now=cutoff_at, record_types=record_types)
        except CleanupAlreadyRunning:
            return False
        try:
            with self.session_factory() as session:
                ProcessingRepository(session).create_job(
                    tenant_id=tenant_id,
                    job_type="retention_cleanup",
                    entity_type="retention_cleanup_run",
                    entity_id=run.id,
                    idempotency_key=f"retention-cleanup:{run.id}",
                    payload={"cleanup_run_id": run.id},
                    next_attempt_at=next_attempt_at,
                )
                session.commit()
        except Exception as exc:
            # Run creation uses a separate transaction. Without this failure
            # transition a stranded pending run blocks all future scheduling.
            RetentionCleanupService(self.session_factory, self.settings).record_failure(
                tenant_id=tenant_id, run_id=run.id, error_code=type(exc).__name__,
            )
            raise
        return True

    def schedule_known_tenants(self, *, now: datetime | None = None) -> int:
        if not self.settings.RETENTION_CLEANUP_ENABLED:
            return 0
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            policy_tenants = set(session.scalars(select(TenantProcessingPolicyModel.tenant_id).where(
                TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            )))
            log_tenants = set(session.scalars(select(LogApplicationModel.tenant_id)))
            tenants = tuple(sorted(policy_tenants | log_tenants))
        return sum(
            int(self.schedule_tenant(tenant_id, cutoff_at=current, next_attempt_at=current))
            for tenant_id in tenants
        )
