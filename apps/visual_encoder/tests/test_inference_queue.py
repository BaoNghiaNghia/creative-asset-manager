from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest


ENCODER_ROOT = Path(__file__).resolve().parents[1]
if str(ENCODER_ROOT) not in sys.path:
    sys.path.insert(0, str(ENCODER_ROOT))

from inference_queue import (
    BoundedInferenceQueue,
    InferenceQueueFullError,
    InferenceQueueTimeoutError,
)


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.005)


def test_interactive_work_runs_before_queued_background_work() -> None:
    async def verify() -> None:
        queue = BoundedInferenceQueue(
            max_queue_size=4,
            interactive_reserve=1,
            wait_timeout_seconds=1.0,
        )
        await queue.start()
        release = threading.Event()
        order: list[str] = []

        def active_background():
            order.append("active-background")
            release.wait(timeout=2)
            return "active"

        def queued_background():
            order.append("queued-background")
            return "background"

        def interactive():
            order.append("interactive")
            return "interactive"

        active_task = asyncio.create_task(
            queue.submit(active_background, priority="background")
        )
        await _wait_until(lambda: queue.snapshot()["active"] is True)

        background_task = asyncio.create_task(
            queue.submit(queued_background, priority="background")
        )
        interactive_task = asyncio.create_task(
            queue.submit(interactive, priority="interactive")
        )
        await _wait_until(lambda: queue.snapshot()["queued"] == 2)

        release.set()
        assert await asyncio.gather(
            active_task,
            background_task,
            interactive_task,
        ) == ["active", "background", "interactive"]
        assert order == [
            "active-background",
            "interactive",
            "queued-background",
        ]
        wait = queue.snapshot()["queue_wait_ms"]
        assert wait["sample_count"] == 3
        assert wait["p95"] >= wait["p50"] >= 0.0
        assert wait["max"] >= wait["p95"]
        await queue.close()

    asyncio.run(verify())


def test_background_cannot_consume_interactive_reserved_slot() -> None:
    async def verify() -> None:
        queue = BoundedInferenceQueue(
            max_queue_size=3,
            interactive_reserve=1,
            wait_timeout_seconds=1.0,
        )
        await queue.start()
        release = threading.Event()

        active_task = asyncio.create_task(
            queue.submit(
                lambda: (release.wait(timeout=2), "active")[1],
                priority="background",
            )
        )
        await _wait_until(lambda: queue.snapshot()["active"] is True)

        first = asyncio.create_task(
            queue.submit(lambda: "b1", priority="background")
        )
        second = asyncio.create_task(
            queue.submit(lambda: "b2", priority="background")
        )
        await _wait_until(lambda: queue.snapshot()["background_queued"] == 2)

        with pytest.raises(InferenceQueueFullError):
            await queue.submit(lambda: "b3", priority="background")
        assert queue.snapshot()["queue_full_total"] == 1

        foreground = asyncio.create_task(
            queue.submit(lambda: "interactive", priority="interactive")
        )
        await _wait_until(lambda: queue.snapshot()["queued"] == 3)

        release.set()
        assert await active_task == "active"
        assert await foreground == "interactive"
        assert sorted(await asyncio.gather(first, second)) == ["b1", "b2"]
        await queue.close()

    asyncio.run(verify())


def test_queue_wait_timeout_removes_request_before_inference() -> None:
    async def verify() -> None:
        queue = BoundedInferenceQueue(
            max_queue_size=2,
            interactive_reserve=1,
            wait_timeout_seconds=0.05,
        )
        await queue.start()
        release = threading.Event()
        executed = threading.Event()

        active_task = asyncio.create_task(
            queue.submit(
                lambda: (release.wait(timeout=2), "active")[1],
                priority="interactive",
            )
        )
        await _wait_until(lambda: queue.snapshot()["active"] is True)

        with pytest.raises(InferenceQueueTimeoutError):
            await queue.submit(
                lambda: (executed.set(), "late")[1],
                priority="interactive",
            )

        assert queue.snapshot()["queued"] == 0
        assert queue.snapshot()["queue_timeout_total"] == 1
        release.set()
        assert await active_task == "active"
        await queue.close()
        assert executed.is_set() is False

    asyncio.run(verify())


def test_cancelled_waiter_is_removed_without_running() -> None:
    async def verify() -> None:
        queue = BoundedInferenceQueue(
            max_queue_size=2,
            interactive_reserve=1,
            wait_timeout_seconds=1.0,
        )
        await queue.start()
        release = threading.Event()
        executed = threading.Event()

        active_task = asyncio.create_task(
            queue.submit(
                lambda: (release.wait(timeout=2), "active")[1],
                priority="interactive",
            )
        )
        await _wait_until(lambda: queue.snapshot()["active"] is True)

        waiting = asyncio.create_task(
            queue.submit(
                lambda: (executed.set(), "cancelled")[1],
                priority="interactive",
            )
        )
        await _wait_until(lambda: queue.snapshot()["queued"] == 1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await _wait_until(lambda: queue.snapshot()["queued"] == 0)
        assert queue.snapshot()["cancelled_total"] == 1

        release.set()
        assert await active_task == "active"
        await queue.close()
        assert executed.is_set() is False

    asyncio.run(verify())
