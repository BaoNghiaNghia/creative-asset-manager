from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.modules.processing.model import OutboxEventModel, ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


class ProcessingJobService:
    """Transaction boundary for worker state transitions."""

    def __init__(self, repository: ProcessingRepository):
        self.repository = repository

    def enqueue_job(self, **kwargs: Any) -> ProcessingJobModel:
        job = self.repository.create_job(**kwargs)
        self.repository.session.commit()
        return job

    def enqueue_job_with_outbox(
        self,
        *,
        job: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> tuple[ProcessingJobModel, OutboxEventModel]:
        queued_job = self.repository.create_job(**job)
        outbox_event = self.repository.create_outbox_event(**event)
        self.repository.session.commit()
        return queued_job, outbox_event

    def claim_next(
        self, *, worker_id: str, lease_seconds: int, now: datetime | None = None
    ) -> ProcessingJobModel | None:
        job = self.repository.claim_next_job(
            worker_id=worker_id, lease_seconds=lease_seconds, now=now
        )
        self.repository.session.commit()
        return job

    def complete(self, *, job_id: str, worker_id: str) -> ProcessingJobModel:
        job = self.repository.complete_job(job_id=job_id, worker_id=worker_id)
        self.repository.session.commit()
        return job

    def fail(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> ProcessingJobModel:
        job = self.repository.fail_job(
            job_id=job_id,
            worker_id=worker_id,
            error_code=error_code,
            error_message=error_message,
        )
        self.repository.session.commit()
        return job

    def renew_lease(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> ProcessingJobModel:
        job = self.repository.renew_job_lease(
            job_id=job_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        self.repository.session.commit()
        return job

    def fail_non_retryable(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> ProcessingJobModel:
        job = self.repository.fail_job_non_retryable(
            job_id=job_id,
            worker_id=worker_id,
            error_code=error_code,
            error_message=error_message,
        )
        self.repository.session.commit()
        return job

    def release(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str = "worker_interrupted",
        error_message: str = "Worker released the job during shutdown.",
    ) -> ProcessingJobModel:
        job = self.repository.release_job(
            job_id=job_id,
            worker_id=worker_id,
            error_code=error_code,
            error_message=error_message,
        )
        self.repository.session.commit()
        return job
