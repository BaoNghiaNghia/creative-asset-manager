from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.retention.service import CleanupAlreadyRunning, RetentionCleanupService


class RetentionCleanupScheduler:
    """Schedules cleanup in the existing durable worker queue."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings

    def schedule_tenant(
        self, tenant_id: str, *, cutoff_at: datetime, next_attempt_at: datetime
    ) -> bool:
        try:
            run = RetentionCleanupService(
                self.session_factory, self.settings
            ).create_run(tenant_id=tenant_id, now=cutoff_at)
        except CleanupAlreadyRunning:
            return False
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
        return True

    def schedule_known_tenants(self, *, now: datetime | None = None) -> int:
        if not self.settings.RETENTION_CLEANUP_ENABLED:
            return 0
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            tenants = tuple(session.scalars(select(TenantProcessingPolicyModel.tenant_id).where(
                TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            )))
        return sum(
            int(self.schedule_tenant(tenant_id, cutoff_at=current, next_attempt_at=current))
            for tenant_id in tenants
        )
