from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Any, Protocol

from sqlalchemy.orm import Session


class JobOutcome(str, Enum):
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobHandlerResult:
    outcome: JobOutcome
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def completed(cls) -> "JobHandlerResult":
        return cls(JobOutcome.COMPLETED)

    @classmethod
    def retryable(cls, code: str, message: str) -> "JobHandlerResult":
        return cls(JobOutcome.RETRYABLE_FAILURE, code, message)

    @classmethod
    def non_retryable(cls, code: str, message: str) -> "JobHandlerResult":
        return cls(JobOutcome.NON_RETRYABLE_FAILURE, code, message)

    @classmethod
    def cancelled(cls, message: str = "Job execution was interrupted.") -> "JobHandlerResult":
        return cls(JobOutcome.CANCELLED, "worker_interrupted", message)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    tenant_id: str
    job_type: str
    entity_type: str
    entity_id: str
    payload: Mapping[str, Any]
    attempt_count: int
    lease_owner: str

    @property
    def correlation_id(self) -> str | None:
        value = self.payload.get("correlation_id")
        return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class WorkerDependencies:
    session_factory: Callable[[], Session]
    source_provider_factory: Callable[..., Any] | None = None
    storage_provider: Any | None = None
    ai_provider: Any | None = None
    resources: Mapping[str, Any] = field(default_factory=dict)
    closers: tuple[Callable[[], Any], ...] = ()
    _closed: bool = field(default=False, init=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for closer in reversed(self.closers):
            try:
                closer()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


@dataclass(frozen=True, slots=True)
class JobHandlerContext:
    job: ClaimedJob
    dependencies: WorkerDependencies
    shutdown_requested: Event
    cancellation_requested: Event
    logger: logging.LoggerAdapter

    @property
    def is_cancelled(self) -> bool:
        return self.cancellation_requested.is_set()


class JobHandler(Protocol):
    def __call__(self, context: JobHandlerContext) -> JobHandlerResult: ...
