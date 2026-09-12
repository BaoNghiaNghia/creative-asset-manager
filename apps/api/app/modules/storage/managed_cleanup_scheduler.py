from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.processing.types import JobStatus
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.storage.repository import ManagedStorageRepository

_IMAGE_PIPELINE_JOB_TYPES = frozenset({
    "source_asset_download", "asset_store", "asset_analyze", "search_projection_build",
    "asset_index", "search_index_sync", "visual_index_sync", "image_generate",
})
_ACTIVE = (JobStatus.PENDING.value, JobStatus.RETRY.value, JobStatus.PROCESSING.value)
_WAITING = (JobStatus.PENDING.value, JobStatus.RETRY.value)


@dataclass(frozen=True, slots=True)
class ManagedStorageCleanupPolicy:
    priority: int
    interval_seconds: int
    batch_size: int
    reason: str


class ManagedStorageCleanupScheduler:
    """Schedules bounded cleanup without allowing it to monopolize Image workers."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings

    def _policy(self, session: Session, tenant_id: str, now: datetime) -> ManagedStorageCleanupPolicy:
        hour = now.astimezone().hour
        start, end = self.settings.MANAGED_STORAGE_CLEANUP_PEAK_START_HOUR, self.settings.MANAGED_STORAGE_CLEANUP_PEAK_END_HOUR
        peak = start <= hour < end if start < end else hour >= start or hour < end
        image_backlog = session.scalar(select(ProcessingJobModel.id).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type.in_(_IMAGE_PIPELINE_JOB_TYPES),
            ProcessingJobModel.status.in_(_ACTIVE),
        ).limit(1)) is not None
        pressure = False
        maximum = self.settings.MANAGED_STORAGE_STAGING_MAX_BYTES
        folder = str(self.settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
        if maximum > 0 and folder:
            used = ManagedStorageRepository(session).staging_bytes_used(remote_folder_id=folder)
            pressure = used * 100 >= maximum * self.settings.MANAGED_STORAGE_CLEANUP_PRESSURE_PERCENT
            critical = used * 100 >= maximum * self.settings.MANAGED_STORAGE_CLEANUP_CRITICAL_PERCENT
            if critical:
                return ManagedStorageCleanupPolicy(95, 60, self.settings.MANAGED_STORAGE_CLEANUP_OFFPEAK_BATCH_SIZE, "critical_capacity")
        if pressure:
            return ManagedStorageCleanupPolicy(80, 60, self.settings.MANAGED_STORAGE_CLEANUP_OFFPEAK_BATCH_SIZE, "capacity_pressure")
        if peak:
            return ManagedStorageCleanupPolicy(
                10 if image_backlog else 30,
                self.settings.MANAGED_STORAGE_CLEANUP_PEAK_INTERVAL_SECONDS,
                self.settings.MANAGED_STORAGE_CLEANUP_PEAK_BATCH_SIZE,
                "peak_with_image_backlog" if image_backlog else "peak_idle",
            )
        return ManagedStorageCleanupPolicy(
            50 if image_backlog else 80,
            self.settings.MANAGED_STORAGE_CLEANUP_INTERVAL_SECONDS,
            self.settings.MANAGED_STORAGE_CLEANUP_OFFPEAK_BATCH_SIZE,
            "offpeak_with_image_backlog" if image_backlog else "offpeak_idle",
        )

    def schedule_tenant(self, tenant_id: str, *, next_attempt_at: datetime) -> bool:
        with self.session_factory() as session:
            policy = self._policy(session, tenant_id, next_attempt_at)
            existing = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "managed_storage_cleanup",
                ProcessingJobModel.status.in_(_ACTIVE),
            ).order_by(ProcessingJobModel.created_at).limit(1))
            if existing is not None:
                if existing.status in _WAITING:
                    existing.priority = policy.priority
                    existing.payload_json = {"dry_run": False, "limit": policy.batch_size, "interval_seconds": policy.interval_seconds, "reason": policy.reason}
                    session.commit()
                    return True
                session.rollback()
                return False
            bucket = int(next_attempt_at.timestamp() // policy.interval_seconds)
            job = ProcessingRepository(session).create_job(
                tenant_id=tenant_id, job_type="managed_storage_cleanup",
                entity_type="managed_storage_cleanup", entity_id=f"{tenant_id}:{bucket}",
                idempotency_key=f"managed-storage-cleanup:{tenant_id}:{bucket}",
                payload={"dry_run": False, "limit": policy.batch_size, "interval_seconds": policy.interval_seconds, "reason": policy.reason},
                priority=policy.priority, next_attempt_at=next_attempt_at,
            )
            session.commit()
            return job.status in _WAITING

    def schedule_known_tenants(self, *, now: datetime | None = None) -> int:
        if not self.settings.MANAGED_STORAGE_AUTO_CLEANUP_ENABLED:
            return 0
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            tenants = tuple(session.scalars(select(TenantProcessingPolicyModel.tenant_id).where(
                TenantProcessingPolicyModel.pipeline_enabled.is_(True),
            )))
        return sum(int(self.schedule_tenant(tenant, next_attempt_at=current)) for tenant in tenants)
