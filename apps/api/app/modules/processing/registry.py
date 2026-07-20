from __future__ import annotations

from collections.abc import Iterable

from app.domain.processing.handlers import JobHandler, JobHandlerContext, JobHandlerResult
from app.domain.processing.types import JOB_TYPES

OUTBOX_DISPATCH_JOB_TYPE = "outbox_dispatch"
WORKER_HANDLER_TYPES = (*JOB_TYPES, OUTBOX_DISPATCH_JOB_TYPE)


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if not job_type:
            raise ValueError("job_type is required")
        if job_type in self._handlers:
            raise ValueError(f"Handler already registered for {job_type}")
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    @property
    def job_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)


class UnsupportedJobHandler:
    def __init__(self, job_type: str):
        self.job_type = job_type

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        return JobHandlerResult.non_retryable(
            "unsupported_handler",
            f"Job type '{self.job_type}' has no production handler in this runtime.",
        )


def build_handler_registry(
    implemented: Iterable[tuple[str, JobHandler]] = (),
) -> HandlerRegistry:
    supplied = dict(implemented)
    unknown = set(supplied) - set(WORKER_HANDLER_TYPES)
    if unknown:
        raise ValueError(f"Unknown worker handler types: {sorted(unknown)}")
    registry = HandlerRegistry()
    for job_type in WORKER_HANDLER_TYPES:
        registry.register(job_type, supplied.get(job_type) or UnsupportedJobHandler(job_type))
    return registry
