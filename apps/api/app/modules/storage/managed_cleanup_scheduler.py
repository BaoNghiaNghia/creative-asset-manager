from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.processing.types import JobStatus
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel


# This is intentionally above tenant-configurable 0-100 job priorities.  Cleanup
# releases the bounded managed-storage staging area; allowing it to starve can
# halt every downstream pipeline stage.
MANAGED_STORAGE_CLEANUP_PRIORITY = 1_000


class ManagedStorageCleanupScheduler:
    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings

    def schedule_tenant(self, tenant_id: str, *, next_attempt_at: datetime) -> bool:
        bucket = int(next_attempt_at.timestamp() // self.settings.MANAGED_STORAGE_CLEANUP_INTERVAL_SECONDS)
        with self.session_factory() as session:
            job = ProcessingRepository(session).create_job(
                tenant_id=tenant_id,
                job_type="managed_storage_cleanup",
                entity_type="managed_storage_cleanup",
                entity_id=f"{tenant_id}:{bucket}",
                idempotency_key=f"managed-storage-cleanup:{tenant_id}:{bucket}",
                payload={"dry_run": False},
                priority=MANAGED_STORAGE_CLEANUP_PRIORITY,
                next_attempt_at=next_attempt_at,
            )
            # Repair jobs created before cleanup became a maintenance-priority
            # task as well as any stale job whose configuration lowered it.
            session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "managed_storage_cleanup",
                    ProcessingJobModel.status.in_(
                        (JobStatus.PENDING.value, JobStatus.RETRY.value)
                    ),
                    ProcessingJobModel.priority < MANAGED_STORAGE_CLEANUP_PRIORITY,
                )
                .values(priority=MANAGED_STORAGE_CLEANUP_PRIORITY)
                .execution_options(synchronize_session=False)
            )
            session.commit()
            return job.status in {"pending", "retry"}

    def schedule_known_tenants(self, *, now: datetime | None = None) -> int:
        if not self.settings.MANAGED_STORAGE_AUTO_CLEANUP_ENABLED:
            return 0
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            tenants = tuple(session.scalars(select(TenantProcessingPolicyModel.tenant_id).where(
                TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            )))
        return sum(int(self.schedule_tenant(tenant, next_attempt_at=current)) for tenant in tenants)
