from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.service import ProcessingJobService

JobHandler = Callable[[ProcessingJobModel], None]


class ProcessingWorker:
    """Opt-in worker loop; constructing this class never starts background work."""

    def __init__(
        self,
        *,
        service: ProcessingJobService,
        worker_id: str,
        handlers: Mapping[str, JobHandler],
        enabled: bool = False,
        lease_seconds: int = 60,
        idle_poll_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.service = service
        self.worker_id = worker_id
        self.handlers = handlers
        self.enabled = enabled
        self.lease_seconds = lease_seconds
        self.idle_poll_seconds = idle_poll_seconds
        self.sleep = sleep

    def run_once(self) -> bool:
        if not self.enabled:
            return False
        job = self.service.claim_next(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds
        )
        if job is None:
            self.sleep(self.idle_poll_seconds)
            return False
        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.service.fail(
                job_id=job.id,
                worker_id=self.worker_id,
                error_code="handler_missing",
                error_message=f"No handler registered for {job.job_type}.",
            )
            return True
        try:
            handler(job)
        except Exception as exc:  # worker boundary records handler failures
            self.service.fail(
                job_id=job.id,
                worker_id=self.worker_id,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        else:
            self.service.complete(job_id=job.id, worker_id=self.worker_id)
        return True
