from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.redaction import redact_url_queries
from app.domain.processing.types import JOB_TYPES, JobStatus, OutboxStatus
from app.modules.processing.model import OutboxEventModel, ProcessingJobModel
from app.modules.processing_policy.claim import TenantAwareJobClaimer


class JobOwnershipError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcessingRepository:
    """Persistence boundary; methods flush but never commit caller transactions."""

    def __init__(self, session: Session):
        self.session = session

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
        next_attempt_at: datetime | None = None,
        provider_key: str | None = None,
        provider_scope: str | None = None,
    ) -> ProcessingJobModel:
        if job_type not in JOB_TYPES:
            raise ValueError(f"Unsupported job type: {job_type}")
        existing = self._job_by_key(tenant_id, idempotency_key)
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                job = ProcessingJobModel(
                    tenant_id=tenant_id,
                    job_type=job_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    idempotency_key=idempotency_key,
                    payload_json=dict(payload or {}),
                    priority=priority,
                    provider_key=provider_key,
                    provider_scope=provider_scope,
                    max_attempts=max_attempts,
                    next_attempt_at=next_attempt_at or utcnow(),
                )
                self.session.add(job)
                self.session.flush()
            return job
        except IntegrityError:
            existing = self._job_by_key(tenant_id, idempotency_key)
            if existing is None:
                raise
            return existing

    def create_outbox_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any] | None = None,
        max_attempts: int = 10,
        next_attempt_at: datetime | None = None,
    ) -> OutboxEventModel:
        existing = self._outbox_by_key(tenant_id, idempotency_key)
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                event = OutboxEventModel(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    idempotency_key=idempotency_key,
                    payload_json=dict(payload or {}),
                    max_attempts=max_attempts,
                    next_attempt_at=next_attempt_at or utcnow(),
                )
                self.session.add(event)
                self.session.flush()
            return event
        except IntegrityError:
            existing = self._outbox_by_key(tenant_id, idempotency_key)
            if existing is None:
                raise
            return existing

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
        enforce_tenant_policy: bool = False,
        allowed_job_types: tuple[str, ...] = JOB_TYPES,
    ) -> ProcessingJobModel | None:
        claimed_at = now or utcnow()
        self._terminalize_exhausted_jobs(claimed_at)
        eligibility = self._job_eligibility(claimed_at)
        lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)

        if enforce_tenant_policy:
            return TenantAwareJobClaimer(self.session).claim(
                worker_id=worker_id, lease_seconds=lease_seconds,
                now=claimed_at, allowed_job_types=allowed_job_types,
            )

        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            job = self.session.scalar(
                select(ProcessingJobModel)
                .where(eligibility)
                .order_by(
                    ProcessingJobModel.priority.desc(),
                    ProcessingJobModel.next_attempt_at,
                    ProcessingJobModel.created_at,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = JobStatus.PROCESSING.value
            job.claimed_by = worker_id
            job.claimed_at = claimed_at
            job.lease_expires_at = lease_expires_at
            job.attempt_count += 1
            job.updated_at = claimed_at
            self.session.flush()
            return job

        candidate = (
            select(ProcessingJobModel.id)
            .where(eligibility)
            .order_by(
                ProcessingJobModel.priority.desc(),
                ProcessingJobModel.next_attempt_at,
                ProcessingJobModel.created_at,
            )
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(ProcessingJobModel)
            .where(ProcessingJobModel.id == candidate, eligibility)
            .values(
                status=JobStatus.PROCESSING.value,
                claimed_by=worker_id,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
                attempt_count=ProcessingJobModel.attempt_count + 1,
                updated_at=claimed_at,
            )
            .returning(ProcessingJobModel)
            .execution_options(synchronize_session=False)
        )
        return self.session.scalars(statement).first()

    def renew_job_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ProcessingJobModel:
        renewed_at = now or utcnow()
        job = self._owned_processing_job(job_id, worker_id)
        job.lease_expires_at = renewed_at + timedelta(seconds=lease_seconds)
        job.updated_at = renewed_at
        self.session.flush()
        return job

    def complete_job(
        self, *, job_id: str, worker_id: str, now: datetime | None = None
    ) -> ProcessingJobModel:
        completed_at = now or utcnow()
        job = self.session.get(ProcessingJobModel, job_id)
        if job is None:
            raise LookupError(job_id)
        if job.status == JobStatus.COMPLETED.value:
            return job
        self._assert_owned(job, worker_id)
        self._record_execution_duration(job, completed_at)
        job.status = JobStatus.COMPLETED.value
        job.completed_at = completed_at
        job.claimed_by = None
        job.claimed_at = None
        TenantAwareJobClaimer(self.session).release(job)
        job.lease_expires_at = None
        job.last_error_code = None
        job.last_error_message = None
        job.updated_at = completed_at
        self.session.flush()
        return job

    def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        base_backoff_seconds: int = 5,
        max_backoff_seconds: int = 3600,
        now: datetime | None = None,
    ) -> ProcessingJobModel:
        failed_at = now or utcnow()
        job = self._owned_processing_job(job_id, worker_id)
        self._record_execution_duration(job, failed_at)
        job.last_error_code = error_code[:100]
        job.last_error_message = redact_url_queries(error_message)
        job.claimed_by = None
        job.claimed_at = None
        TenantAwareJobClaimer(self.session).release(job)
        job.lease_expires_at = None
        job.updated_at = failed_at
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED.value
            job.completed_at = failed_at
        else:
            exponent = max(job.attempt_count - 1, 0)
            delay = min(base_backoff_seconds * (2**exponent), max_backoff_seconds)
            job.status = JobStatus.RETRY.value
            job.next_attempt_at = failed_at + timedelta(seconds=delay)
        self.session.flush()
        return job

    def fail_job_non_retryable(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> ProcessingJobModel:
        failed_at = now or utcnow()
        job = self._owned_processing_job(job_id, worker_id)
        self._record_execution_duration(job, failed_at)
        job.status = JobStatus.FAILED.value
        job.completed_at = failed_at
        job.last_error_code = error_code[:100]
        job.last_error_message = redact_url_queries(error_message)
        TenantAwareJobClaimer(self.session).release(job)
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.updated_at = failed_at
        self.session.flush()
        return job

    def release_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str = "worker_interrupted",
        error_message: str = "Worker released the job during shutdown.",
        now: datetime | None = None,
    ) -> ProcessingJobModel:
        released_at = now or utcnow()
        job = self._owned_processing_job(job_id, worker_id)
        self._record_execution_duration(job, released_at)
        if job.cancellation_requested:
            job.status = JobStatus.FAILED.value
            job.completed_at = released_at
            job.last_error_code = "operation_cancelled"
            job.last_error_message = "Cancelled by an authorized operator."
        else:
            job.status = JobStatus.RETRY.value
            job.next_attempt_at = released_at
            job.last_error_code = error_code[:100]
            job.last_error_message = redact_url_queries(error_message)
        TenantAwareJobClaimer(self.session).release(job)
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.updated_at = released_at
        self.session.flush()
        return job

    def defer_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        retry_at: datetime,
        reason_code: str,
        reason_message: str,
        now: datetime | None = None,
    ) -> ProcessingJobModel:
        if retry_at.tzinfo is None or retry_at.utcoffset() is None:
            raise ValueError("retry_at must be timezone-aware")
        deferred_at = now or utcnow()
        job = self._owned_processing_job(job_id, worker_id)
        self._record_execution_duration(job, deferred_at)
        TenantAwareJobClaimer(self.session).release(job)
        job.status = JobStatus.PENDING.value
        job.next_attempt_at = retry_at
        # Claiming increments attempts. Deferral is not an execution failure.
        job.attempt_count = max(0, job.attempt_count - 1)
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.completed_at = None
        job.last_error_code = reason_code[:100]
        job.last_error_message = redact_url_queries(reason_message)
        job.updated_at = deferred_at
        self.session.flush()
        return job

    def claim_next_outbox_event(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> OutboxEventModel | None:
        claimed_at = now or utcnow()
        self._terminalize_exhausted_events(claimed_at)
        eligibility = self._outbox_eligibility(claimed_at)
        lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            event = self.session.scalar(
                select(OutboxEventModel)
                .where(eligibility)
                .order_by(OutboxEventModel.next_attempt_at, OutboxEventModel.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            event.status = OutboxStatus.PROCESSING.value
            event.claimed_by = worker_id
            event.claimed_at = claimed_at
            event.lease_expires_at = lease_expires_at
            event.attempt_count += 1
            event.updated_at = claimed_at
            self.session.flush()
            return event
        candidate = (
            select(OutboxEventModel.id)
            .where(eligibility)
            .order_by(OutboxEventModel.next_attempt_at, OutboxEventModel.created_at)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(OutboxEventModel)
            .where(OutboxEventModel.id == candidate, eligibility)
            .values(
                status=OutboxStatus.PROCESSING.value,
                claimed_by=worker_id,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
                attempt_count=OutboxEventModel.attempt_count + 1,
                updated_at=claimed_at,
            )
            .returning(OutboxEventModel)
            .execution_options(synchronize_session=False)
        )
        return self.session.scalars(statement).first()

    def publish_outbox_event(
        self, *, event_id: str, worker_id: str, now: datetime | None = None
    ) -> OutboxEventModel:
        published_at = now or utcnow()
        event = self.session.get(OutboxEventModel, event_id)
        if event is None:
            raise LookupError(event_id)
        if event.status == OutboxStatus.PUBLISHED.value:
            return event
        if event.status != OutboxStatus.PROCESSING.value or event.claimed_by != worker_id:
            raise JobOwnershipError(event_id)
        event.status = OutboxStatus.PUBLISHED.value
        event.published_at = published_at
        event.claimed_by = None
        event.claimed_at = None
        event.lease_expires_at = None
        event.updated_at = published_at
        self.session.flush()
        return event

    def fail_outbox_event(
        self,
        *,
        event_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        base_backoff_seconds: int = 5,
        max_backoff_seconds: int = 3600,
        now: datetime | None = None,
    ) -> OutboxEventModel:
        failed_at = now or utcnow()
        event = self.session.get(OutboxEventModel, event_id)
        if event is None:
            raise LookupError(event_id)
        if event.status != OutboxStatus.PROCESSING.value or event.claimed_by != worker_id:
            raise JobOwnershipError(event_id)
        event.last_error_code = error_code[:100]
        event.last_error_message = redact_url_queries(error_message)
        event.claimed_by = None
        event.claimed_at = None
        event.lease_expires_at = None
        event.updated_at = failed_at
        if event.attempt_count >= event.max_attempts:
            event.status = OutboxStatus.FAILED.value
        else:
            exponent = max(event.attempt_count - 1, 0)
            delay = min(base_backoff_seconds * (2**exponent), max_backoff_seconds)
            event.status = OutboxStatus.PENDING.value
            event.next_attempt_at = failed_at + timedelta(seconds=delay)
        self.session.flush()
        return event

    def get_job(self, job_id: str) -> ProcessingJobModel | None:
        return self.session.get(ProcessingJobModel, job_id)

    def cancel_unstarted_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ProcessingJobModel | None:
        cancelled_at = now or utcnow()
        job = self.session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.id == job_id,
            )
        )
        if job is None:
            return None
        if job.status not in {JobStatus.PENDING.value, JobStatus.RETRY.value}:
            return job
        job.status = JobStatus.FAILED.value
        job.completed_at = cancelled_at
        job.last_error_code = "operation_cancelled"
        job.last_error_message = redact_url_queries(
            f"Cancelled by {actor_id}: {reason}"
        )
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.updated_at = cancelled_at
        self.session.flush()
        return job

    @staticmethod
    def _record_execution_duration(job: ProcessingJobModel, ended_at: datetime) -> None:
        """Accumulate active worker time while excluding queue and retry-backoff time."""
        if job.claimed_at is None:
            return
        started_at = job.claimed_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=timezone.utc)
        elapsed_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))
        job.processing_duration_ms = int(job.processing_duration_ms or 0) + elapsed_ms

    def _job_by_key(self, tenant_id: str, key: str) -> ProcessingJobModel | None:
        return self.session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.idempotency_key == key,
            )
        )

    def _outbox_by_key(self, tenant_id: str, key: str) -> OutboxEventModel | None:
        return self.session.scalar(
            select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.idempotency_key == key,
            )
        )

    @staticmethod
    def _job_eligibility(now: datetime):
        return and_(
            ProcessingJobModel.cancellation_requested.is_(False),
            ProcessingJobModel.attempt_count < ProcessingJobModel.max_attempts,
            or_(
                and_(
                    ProcessingJobModel.status.in_(
                        (JobStatus.PENDING.value, JobStatus.RETRY.value)
                    ),
                    ProcessingJobModel.next_attempt_at <= now,
                ),
                and_(
                    ProcessingJobModel.status == JobStatus.PROCESSING.value,
                    ProcessingJobModel.lease_expires_at <= now,
                ),
            ),
        )

    @staticmethod
    def _outbox_eligibility(now: datetime):
        return and_(
            OutboxEventModel.attempt_count < OutboxEventModel.max_attempts,
            or_(
                and_(
                    OutboxEventModel.status == OutboxStatus.PENDING.value,
                    OutboxEventModel.next_attempt_at <= now,
                ),
                and_(
                    OutboxEventModel.status == OutboxStatus.PROCESSING.value,
                    OutboxEventModel.lease_expires_at <= now,
                ),
            ),
        )

    def _terminalize_exhausted_jobs(self, now: datetime) -> None:
        TenantAwareJobClaimer(self.session).release_exhausted(now)
        self.session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.status == JobStatus.PROCESSING.value,
                ProcessingJobModel.lease_expires_at <= now,
                ProcessingJobModel.attempt_count >= ProcessingJobModel.max_attempts,
            )
            .values(
                status=JobStatus.FAILED.value,
                completed_at=now,
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
                last_error_code="lease_expired",
                last_error_message="Worker lease expired after the final attempt.",
                updated_at=now,
            )
        )

    def _terminalize_exhausted_events(self, now: datetime) -> None:
        self.session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.status == OutboxStatus.PROCESSING.value,
                OutboxEventModel.lease_expires_at <= now,
                OutboxEventModel.attempt_count >= OutboxEventModel.max_attempts,
            )
            .values(
                status=OutboxStatus.FAILED.value,
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
                last_error_code="lease_expired",
                last_error_message="Publisher lease expired after the final attempt.",
                updated_at=now,
            )
        )

    def _owned_processing_job(self, job_id: str, worker_id: str) -> ProcessingJobModel:
        job = self.session.get(ProcessingJobModel, job_id)
        if job is None:
            raise LookupError(job_id)
        self._assert_owned(job, worker_id)
        return job

    @staticmethod
    def _assert_owned(job: ProcessingJobModel, worker_id: str) -> None:
        if job.status != JobStatus.PROCESSING.value or job.claimed_by != worker_id:
            raise JobOwnershipError(job.id)

    def count_jobs(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(ProcessingJobModel)) or 0)
