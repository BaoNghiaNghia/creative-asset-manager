from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.domain.processing.handlers import (
    ClaimedJob,
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
    JobOutcome,
    WorkerDependencies,
)
from app.modules.processing.health import WorkerHealthState
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.registry import HandlerRegistry
from app.modules.processing.repository import JobOwnershipError, ProcessingRepository
from app.modules.processing.service import ProcessingJobService


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    worker_id: str
    worker_role: str = "all"
    enabled: bool = False
    lease_seconds: int = 60
    heartbeat_seconds: float = 15.0
    idle_poll_seconds: float = 2.0
    drain_timeout_seconds: float = 30.0
    enforce_tenant_policy: bool = False
    allowed_job_types: tuple[str, ...] = ()
    borrowed_job_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("worker_id is required")
        if self.worker_role not in {"all", "image", "video"}:
            raise ValueError("worker_role must be one of: all, image, video")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not 0 < self.heartbeat_seconds < self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        if self.idle_poll_seconds <= 0:
            raise ValueError("idle_poll_seconds must be positive")
        if self.drain_timeout_seconds < 0:
            raise ValueError("drain_timeout_seconds cannot be negative")


class _ActiveExecution:
    def __init__(self, target: Callable[[], None], name: str, job: ClaimedJob):
        self.job = job
        self.done = threading.Event()
        self.cancel = threading.Event()
        self.abandoned = threading.Event()
        self.stop_heartbeat = threading.Event()

        def run() -> None:
            try:
                target()
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def abandon(self) -> None:
        self.cancel.set()
        self.abandoned.set()
        self.stop_heartbeat.set()


class WorkerAsyncExecutor:
    """Runs worker-owned async resources on one event loop for its lifetime."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False
        self._lock = threading.Lock()

    def run(self, awaitable):
        self._start()
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("worker async executor is not available")
        return asyncio.run_coroutine_threadsafe(awaitable, loop).result()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("worker async executor is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run_loop,
                name="worker-async-io",
                daemon=True,
            )
            self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()


class WorkerRuntime:
    """Single-concurrency database worker with renewable leases and safe draining."""

    def __init__(
        self,
        *,
        config: WorkerRuntimeConfig,
        dependencies: WorkerDependencies,
        registry: HandlerRegistry,
        health: WorkerHealthState | None = None,
        logger: logging.Logger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.dependencies = dependencies
        self.registry = registry
        self.health = health or WorkerHealthState(config.worker_id)
        self.logger = logger or logging.getLogger("cam.worker")
        self.monotonic = monotonic
        self.shutdown_requested = threading.Event()
        self._active: _ActiveExecution | None = None
        self._active_lock = threading.Lock()
        self._closed = False
        self._async_executor = WorkerAsyncExecutor()
        resources = dict(self.dependencies.resources)
        resources.setdefault("async_executor", self._async_executor)
        self.dependencies.resources = resources

    def start(self) -> None:
        self.health.startup_complete(enabled=self.config.enabled, database_available=True)
        self._log(
            logging.INFO,
            "worker_started",
            processing_enabled=self.config.enabled,
            lease_seconds=self.config.lease_seconds,
            heartbeat_seconds=self.config.heartbeat_seconds,
            idle_poll_seconds=self.config.idle_poll_seconds,
            drain_timeout_seconds=self.config.drain_timeout_seconds,
        )

    def request_shutdown(self) -> None:
        if self.shutdown_requested.is_set():
            return
        self.shutdown_requested.set()
        self.health.start_draining()
        self._log(logging.INFO, "worker_drain_started")

    def run_forever(self) -> None:
        self.start()
        try:
            while not self.shutdown_requested.is_set():
                active = self._get_active()
                if active is not None:
                    if active.done.wait(timeout=0.05):
                        active.thread.join(timeout=0)
                        self._clear_active(active)
                    continue
                if not self.config.enabled:
                    self.shutdown_requested.wait(self.config.idle_poll_seconds)
                    continue
                if not self._claim_and_start():
                    self.shutdown_requested.wait(self.config.idle_poll_seconds)
            self._drain()
        finally:
            self.close()

    def run_once(self) -> bool:
        if not self.config.enabled or self.shutdown_requested.is_set():
            return False
        claimed = self._claim_and_start()
        if not claimed:
            return False
        active = self._get_active()
        if active is not None:
            active.done.wait()
            active.thread.join(timeout=0)
            self._clear_active(active)
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.health.stop()
        try:
            self._close_async_resources()
            self.dependencies.close()
        finally:
            self._async_executor.close()
        self._log(logging.INFO, "worker_stopped")

    def _close_async_resources(self) -> None:
        """Close worker-owned async clients before their event loop stops."""
        for resource in self.dependencies.resources.values():
            closer = getattr(resource, "aclose", None)
            if closer is None:
                continue
            result = closer()
            if inspect.isawaitable(result):
                self._async_executor.run(result)

    def _claim_and_start(self) -> bool:
        if self.shutdown_requested.is_set():
            return False
        try:
            with self.dependencies.session_factory() as session:
                service = ProcessingJobService(ProcessingRepository(session, self.dependencies.settings))
                model = service.claim_next(
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.lease_seconds,
                    enforce_tenant_policy=self.config.enforce_tenant_policy,
                    allowed_job_types=tuple(dict.fromkeys(
                        self.config.allowed_job_types + self.config.borrowed_job_types
                    )),
                    worker_role=self.config.worker_role,
                )
            self.health.set_database_available(True)
        except Exception as exc:
            self.health.set_database_available(False)
            self._log(
                logging.ERROR,
                "worker_poll_failed",
                error_code=type(exc).__name__,
            )
            return False

        self.health.record_poll(claimed=model is not None)
        if model is None:
            self._log(logging.DEBUG, "worker_poll_empty")
            return False

        job = self._snapshot(model)
        active: _ActiveExecution
        active = _ActiveExecution(
            lambda: self._execute(job, active),
            name=f"job-{job.id}",
            job=job,
        )
        with self._active_lock:
            if self.shutdown_requested.is_set():
                return False
            self._active = active
            self.health.set_active_jobs(1)
        self._job_log(logging.INFO, "job_claimed", job)
        active.start()
        return True

    def _execute(self, job: ClaimedJob, active: _ActiveExecution) -> None:
        started = self.monotonic()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job, active, lease_lost),
            name=f"heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat.start()
        handler = self.registry.resolve(job.job_type)
        context = JobHandlerContext(
            job=job,
            dependencies=self.dependencies,
            shutdown_requested=self.shutdown_requested,
            cancellation_requested=active.cancel,
            logger=logging.LoggerAdapter(self.logger, self._job_fields(job)),
        )
        try:
            if handler is None:
                result = JobHandlerResult.non_retryable(
                    "unsupported_handler",
                    f"No handler is registered for job type '{job.job_type}'.",
                )
            else:
                result = handler(context)
                if not isinstance(result, (JobHandlerResult, DeferredJobOutcome)):
                    result = JobHandlerResult.non_retryable(
                        "invalid_handler_result",
                        "Handler did not return a supported job result.",
                    )
        except Exception as exc:
            result = JobHandlerResult.retryable(type(exc).__name__, str(exc))
        finally:
            active.stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.config.heartbeat_seconds + 0.5))

        duration_ms = int((self.monotonic() - started) * 1000)
        if lease_lost.is_set() or active.abandoned.is_set():
            self._job_log(
                logging.WARNING,
                "job_finalization_skipped",
                job,
                duration_ms=duration_ms,
                final_outcome="lease_lost" if lease_lost.is_set() else "interrupted",
            )
            return

        try:
            self._finalize(job, result)
        except JobOwnershipError:
            active.cancel.set()
            self._job_log(
                logging.WARNING,
                "job_lease_lost",
                job,
                duration_ms=duration_ms,
                final_outcome="lease_lost",
            )
            return
        except Exception as exc:
            self.health.set_database_available(False)
            self._job_log(
                logging.ERROR,
                "job_finalization_failed",
                job,
                duration_ms=duration_ms,
                final_outcome="recoverable_by_lease",
                error_code=type(exc).__name__,
            )
            return

        self._job_log(
            logging.INFO,
            "job_finished",
            job,
            duration_ms=duration_ms,
            final_outcome=(
                result.outcome.value
                if isinstance(result, JobHandlerResult)
                else "deferred"
            ),
            error_code=(
                result.error_code
                if isinstance(result, JobHandlerResult)
                else result.reason_code
            ),
        )

    def _heartbeat_loop(
        self,
        job: ClaimedJob,
        active: _ActiveExecution,
        lease_lost: threading.Event,
    ) -> None:
        while not active.stop_heartbeat.wait(self.config.heartbeat_seconds):
            try:
                with self.dependencies.session_factory() as session:
                    renewed = ProcessingJobService(ProcessingRepository(session, self.dependencies.settings)).renew_lease(
                        job_id=job.id,
                        worker_id=self.config.worker_id,
                        lease_seconds=self.config.lease_seconds,
                    )
                if renewed.cancellation_requested:
                    active.cancel.set()
                self._job_log(logging.DEBUG, "job_lease_renewed", job)
            except JobOwnershipError:
                lease_lost.set()
                active.cancel.set()
                active.stop_heartbeat.set()
                self._job_log(logging.WARNING, "job_lease_lost", job)
            except Exception as exc:
                lease_lost.set()
                active.cancel.set()
                active.stop_heartbeat.set()
                self.health.set_database_available(False)
                self._job_log(
                    logging.ERROR,
                    "job_heartbeat_failed",
                    job,
                    error_code=type(exc).__name__,
                )

    def _finalize(
        self, job: ClaimedJob, result: JobHandlerResult | DeferredJobOutcome
    ) -> None:
        with self.dependencies.session_factory() as session:
            service = ProcessingJobService(ProcessingRepository(session, self.dependencies.settings))
            if isinstance(result, DeferredJobOutcome):
                service.defer(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    retry_at=result.retry_at,
                    reason_code=result.reason_code,
                    reason_message=result.reason_message,
                )
            elif result.outcome is JobOutcome.COMPLETED:
                service.complete(job_id=job.id, worker_id=self.config.worker_id)
            elif result.outcome is JobOutcome.RETRYABLE_FAILURE:
                service.fail(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    error_code=result.error_code or "handler_retryable_failure",
                    error_message=result.error_message or "Retryable handler failure.",
                )
            elif result.outcome is JobOutcome.NON_RETRYABLE_FAILURE:
                service.fail_non_retryable(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    error_code=result.error_code or "handler_non_retryable_failure",
                    error_message=result.error_message or "Non-retryable handler failure.",
                )
            else:
                service.release(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    error_code=result.error_code or "worker_interrupted",
                    error_message=result.error_message or "Worker interrupted the job.",
                )
        self.health.set_database_available(True)

    def _drain(self) -> None:
        self.health.start_draining()
        deadline = self.monotonic() + self.config.drain_timeout_seconds
        active = self._get_active()
        if active is None:
            return
        remaining = max(0.0, deadline - self.monotonic())
        if active.done.wait(remaining):
            active.thread.join(timeout=0)
            self._clear_active(active)
            return

        self._log(logging.WARNING, "worker_drain_timeout")
        active.cancel.set()
        grace = min(1.0, self.config.heartbeat_seconds)
        if active.done.wait(grace):
            active.thread.join(timeout=0)
            self._clear_active(active)
            return
        active.abandon()
        self._job_log(
            logging.WARNING,
            "job_abandoned_until_lease_expiry",
            self._active_job_snapshot(),
            final_outcome="recoverable_by_lease",
        )

    def _get_active(self) -> _ActiveExecution | None:
        with self._active_lock:
            return self._active

    def _clear_active(self, active: _ActiveExecution) -> None:
        with self._active_lock:
            if self._active is active:
                self._active = None
                self.health.set_active_jobs(0)

    def _active_job_snapshot(self) -> ClaimedJob:
        active = self._get_active()
        job = getattr(active, "job", None)
        if job is None:
            return ClaimedJob(
                id="unknown",
                tenant_id="unknown",
                job_type="unknown",
                entity_type="unknown",
                entity_id="unknown",
                payload={},
                attempt_count=0,
                lease_owner=self.config.worker_id,
            )
        return job

    @staticmethod
    def _snapshot(model: ProcessingJobModel) -> ClaimedJob:
        return ClaimedJob(
            id=model.id,
            tenant_id=model.tenant_id,
            job_type=model.job_type,
            entity_type=model.entity_type,
            entity_id=model.entity_id,
            payload=dict(model.payload_json or {}),
            attempt_count=model.attempt_count,
            lease_owner=model.claimed_by or "",
        )

    def _job_fields(self, job: ClaimedJob) -> dict[str, object]:
        return {
            "worker_id": self.config.worker_id,
            "job_id": job.id,
            "job_type": job.job_type,
            "tenant_id": job.tenant_id,
            "entity_type": job.entity_type,
            "entity_id": job.entity_id,
            "attempt_count": job.attempt_count,
            "lease_owner": job.lease_owner,
            "correlation_id": job.correlation_id,
        }

    def _job_log(self, level: int, event: str, job: ClaimedJob, **fields: object) -> None:
        self.logger.log(level, event, extra={**self._job_fields(job), **fields})

    def _log(self, level: int, event: str, **fields: object) -> None:
        self.logger.log(level, event, extra={"worker_id": self.config.worker_id, **fields})
