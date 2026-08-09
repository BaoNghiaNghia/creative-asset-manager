from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.model import InventoryProcessingControlModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InventoryJobRepository:
    """Inventory-only persistence boundary; methods flush but never commit."""

    def __init__(self, session: Session, registered_job_types: tuple[str, ...] = ()):
        self.session = session
        self.registered_job_types = registered_job_types

    def create_job(
        self,
        *,
        tenant_id: str,
        job_type: str,
        entity_type: str,
        entity_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any] | None = None,
        priority: int = 0,
        max_attempts: int = 5,
    ) -> InventoryJobModel:
        if job_type not in self.registered_job_types:
            raise ValueError(f"Unregistered Inventory job type: {job_type}")
        existing = self.session.scalar(
            select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                job = InventoryJobModel(
                    tenant_id=tenant_id,
                    job_type=job_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    idempotency_key=idempotency_key,
                    payload_json=dict(payload or {}),
                    priority=priority,
                    max_attempts=max_attempts,
                )
                self.session.add(job)
                self.session.flush()
            return job
        except IntegrityError:
            existing = self.session.scalar(
                select(InventoryJobModel).where(
                    InventoryJobModel.tenant_id == tenant_id,
                    InventoryJobModel.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> InventoryJobModel | None:
        if not self.registered_job_types:
            return None
        claimed_at = now or _utcnow()
        control = InventoryProcessingControlModel
        active_count = (
            select(func.count(InventoryJobModel.id))
            .where(
                InventoryJobModel.tenant_id == control.tenant_id,
                InventoryJobModel.status == "processing",
                InventoryJobModel.lease_expires_at > claimed_at,
            )
            .correlate(control)
            .scalar_subquery()
        )
        eligible = and_(
            InventoryJobModel.job_type.in_(self.registered_job_types),
            InventoryJobModel.cancellation_requested.is_(False),
            InventoryJobModel.attempt_count < InventoryJobModel.max_attempts,
            or_(
                and_(
                    InventoryJobModel.status.in_(("pending", "retry")),
                    InventoryJobModel.next_attempt_at <= claimed_at,
                ),
                and_(
                    InventoryJobModel.status == "processing",
                    InventoryJobModel.lease_expires_at <= claimed_at,
                ),
            ),
        )
        query = (
            select(InventoryJobModel)
            .join(control, control.tenant_id == InventoryJobModel.tenant_id)
            .where(
                eligible,
                control.enabled.is_(True),
                control.paused.is_(False),
                active_count < control.max_active_jobs,
            )
            .order_by(
                InventoryJobModel.priority.desc(),
                InventoryJobModel.next_attempt_at,
                InventoryJobModel.created_at,
            )
            .limit(1)
        )
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True, of=InventoryJobModel)
        job = self.session.scalar(query)
        if job is None:
            return None
        job.status = "processing"
        job.claimed_by = worker_id
        job.claimed_at = claimed_at
        job.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        job.attempt_count += 1
        job.updated_at = claimed_at
        self.session.flush()
        return job

    def complete(self, job: InventoryJobModel, worker_id: str) -> None:
        if job.status != "processing" or job.claimed_by != worker_id:
            raise RuntimeError("Inventory job is not owned by this worker")
        now = _utcnow()
        job.status = "completed"
        job.completed_at = now
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.updated_at = now
        self.session.flush()

    def fail(
        self,
        job: InventoryJobModel,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> bool:
        if job.status != "processing" or job.claimed_by != worker_id:
            raise RuntimeError("Inventory job is not owned by this worker")
        failed_at = now or _utcnow()
        can_retry = retryable and job.attempt_count < job.max_attempts
        job.status = "retry" if can_retry else "failed"
        job.next_attempt_at = (
            failed_at
            + timedelta(seconds=min(300, 2 ** max(0, job.attempt_count - 1)))
            if can_retry
            else failed_at
        )
        job.last_error_code = error_code[:100]
        job.last_error_message = error_message[:1000]
        job.completed_at = None if can_retry else failed_at
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.updated_at = failed_at
        self.session.flush()
        return can_retry
