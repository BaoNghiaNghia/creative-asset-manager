from __future__ import annotations

import asyncio
import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Generic, Literal, TypeVar


T = TypeVar("T")
InferencePriority = Literal["interactive", "background"]


class InferenceQueueFullError(RuntimeError):
    pass


class InferenceQueueTimeoutError(RuntimeError):
    pass


class InferenceQueueClosedError(RuntimeError):
    pass


@dataclass(order=True)
class _QueuedInference(Generic[T]):
    priority: int
    sequence: int
    kind: InferencePriority = field(compare=False)
    enqueued_at: float = field(compare=False)
    operation: Callable[[], T] = field(compare=False)
    started: asyncio.Future[None] = field(compare=False)
    result: asyncio.Future[T] = field(compare=False)


class BoundedInferenceQueue:
    """One-worker priority queue with bounded memory and foreground reservation."""

    _PRIORITY = {"interactive": 0, "background": 10}

    def __init__(
        self,
        *,
        max_queue_size: int,
        interactive_reserve: int,
        wait_timeout_seconds: float,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if interactive_reserve < 0 or interactive_reserve >= max_queue_size:
            raise ValueError(
                "interactive_reserve must be non-negative and smaller than max_queue_size"
            )
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self._max_queue_size = max_queue_size
        self._max_background_queued = max_queue_size - interactive_reserve
        self._wait_timeout_seconds = wait_timeout_seconds
        self._items: list[_QueuedInference] = []
        self._sequence = itertools.count()
        self._condition = asyncio.Condition()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._active_kind: InferencePriority | None = None
        self._background_queued = 0
        self._accepted_total = 0
        self._started_total = 0
        self._completed_total = 0
        self._failed_total = 0
        self._queue_full_total = 0
        self._queue_timeout_total = 0
        self._cancelled_total = 0
        self._wait_samples_ms: deque[float] = deque(maxlen=256)

    async def start(self) -> None:
        if self._worker is not None:
            return
        if self._closed:
            raise InferenceQueueClosedError("inference queue is closed")
        self._worker = asyncio.create_task(
            self._run(),
            name="visual-encoder-inference-queue",
        )

    async def close(self) -> None:
        async with self._condition:
            if self._closed:
                worker = self._worker
            else:
                self._closed = True
                queued = list(self._items)
                self._items.clear()
                self._background_queued = 0
                for job in queued:
                    if not job.started.done():
                        job.started.set_exception(
                            InferenceQueueClosedError("inference queue is closed")
                        )
                    if not job.result.done():
                        job.result.cancel()
                self._condition.notify_all()
                worker = self._worker
        if worker is not None:
            await worker
        self._worker = None

    @staticmethod
    def _percentile(values: tuple[float, ...], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def snapshot(self) -> dict[str, object]:
        wait_samples = tuple(self._wait_samples_ms)
        return {
            "active": self._active_kind is not None,
            "active_priority": self._active_kind,
            "queued": len(self._items),
            "background_queued": self._background_queued,
            "max_queue_size": self._max_queue_size,
            "background_queue_limit": self._max_background_queued,
            "wait_timeout_seconds": self._wait_timeout_seconds,
            "accepted_total": self._accepted_total,
            "started_total": self._started_total,
            "completed_total": self._completed_total,
            "failed_total": self._failed_total,
            "queue_full_total": self._queue_full_total,
            "queue_timeout_total": self._queue_timeout_total,
            "cancelled_total": self._cancelled_total,
            "queue_wait_ms": {
                "sample_count": len(wait_samples),
                "p50": self._percentile(wait_samples, 0.50),
                "p95": self._percentile(wait_samples, 0.95),
                "max": max(wait_samples, default=0.0),
            },
        }

    async def submit(
        self,
        operation: Callable[[], T],
        *,
        priority: InferencePriority,
    ) -> T:
        if priority not in self._PRIORITY:
            raise ValueError("unsupported inference priority")
        loop = asyncio.get_running_loop()
        job = _QueuedInference(
            priority=self._PRIORITY[priority],
            sequence=next(self._sequence),
            kind=priority,
            enqueued_at=loop.time(),
            operation=operation,
            started=loop.create_future(),
            result=loop.create_future(),
        )

        async with self._condition:
            if self._closed:
                raise InferenceQueueClosedError("inference queue is closed")
            if len(self._items) >= self._max_queue_size:
                self._queue_full_total += 1
                raise InferenceQueueFullError("inference queue is full")
            if (
                priority == "background"
                and self._background_queued >= self._max_background_queued
            ):
                self._queue_full_total += 1
                raise InferenceQueueFullError(
                    "background inference queue reservation is exhausted"
                )
            heapq.heappush(self._items, job)
            self._accepted_total += 1
            if priority == "background":
                self._background_queued += 1
            self._condition.notify()

        try:
            await asyncio.wait_for(
                asyncio.shield(job.started),
                timeout=self._wait_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            removed = await self._remove_if_queued(job)
            if removed:
                if not job.started.done():
                    job.started.cancel()
                if not job.result.done():
                    job.result.cancel()
                self._queue_timeout_total += 1
                raise InferenceQueueTimeoutError(
                    "inference queue wait timed out"
                ) from exc
            # The worker won the race and already started inference.
            await asyncio.shield(job.started)
        except asyncio.CancelledError:
            self._cancelled_total += 1
            removed = await self._remove_if_queued(job)
            if removed:
                if not job.started.done():
                    job.started.cancel()
                if not job.result.done():
                    job.result.cancel()
            raise

        try:
            return await asyncio.shield(job.result)
        except asyncio.CancelledError:
            self._cancelled_total += 1
            # Python thread inference cannot be force-cancelled safely once started.
            # Shield it so the single worker owns completion while the request exits.
            raise

    async def _remove_if_queued(self, job: _QueuedInference) -> bool:
        async with self._condition:
            try:
                index = next(
                    index
                    for index, candidate in enumerate(self._items)
                    if candidate is job
                )
            except StopIteration:
                return False
            removed = self._items.pop(index)
            heapq.heapify(self._items)
            if removed.kind == "background":
                self._background_queued -= 1
            return True

    async def _run(self) -> None:
        while True:
            async with self._condition:
                while not self._items and not self._closed:
                    await self._condition.wait()
                if self._closed and not self._items:
                    return
                job = heapq.heappop(self._items)
                if job.kind == "background":
                    self._background_queued -= 1
                if job.result.cancelled():
                    continue
                self._active_kind = job.kind
                self._started_total += 1
                self._wait_samples_ms.append(
                    max(
                        0.0,
                        (
                            asyncio.get_running_loop().time()
                            - job.enqueued_at
                        )
                        * 1000.0,
                    )
                )
                if not job.started.done():
                    job.started.set_result(None)

            try:
                value = await asyncio.to_thread(job.operation)
            except Exception as exc:
                self._failed_total += 1
                if not job.result.done():
                    job.result.set_exception(exc)
            else:
                self._completed_total += 1
                if not job.result.done():
                    job.result.set_result(value)
            finally:
                self._active_kind = None
