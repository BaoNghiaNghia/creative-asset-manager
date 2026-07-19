import unittest
from types import SimpleNamespace

from app.modules.processing.worker import ProcessingWorker


class FakeService:
    def __init__(self, job=None):
        self.job = job
        self.claim_calls = 0
        self.completed: list[str] = []
        self.failed: list[str] = []

    def claim_next(self, **_kwargs):
        self.claim_calls += 1
        job, self.job = self.job, None
        return job

    def complete(self, *, job_id, worker_id):
        self.completed.append(f"{worker_id}:{job_id}")

    def fail(self, *, job_id, worker_id, **_kwargs):
        self.failed.append(f"{worker_id}:{job_id}")


class ProcessingWorkerTest(unittest.TestCase):
    def test_disabled_worker_never_claims(self) -> None:
        service = FakeService()
        worker = ProcessingWorker(
            service=service,
            worker_id="worker-a",
            handlers={},
            enabled=False,
        )
        self.assertFalse(worker.run_once())
        self.assertEqual(service.claim_calls, 0)

    def test_empty_queue_waits_before_next_poll(self) -> None:
        service = FakeService()
        sleeps: list[float] = []
        worker = ProcessingWorker(
            service=service,
            worker_id="worker-a",
            handlers={},
            enabled=True,
            idle_poll_seconds=2.5,
            sleep=sleeps.append,
        )
        self.assertFalse(worker.run_once())
        self.assertEqual(sleeps, [2.5])

    def test_handler_completion_uses_claimed_job_identity(self) -> None:
        job = SimpleNamespace(id="job-1", job_type="asset_store")
        service = FakeService(job)
        handled: list[str] = []
        worker = ProcessingWorker(
            service=service,
            worker_id="worker-a",
            handlers={"asset_store": lambda claimed: handled.append(claimed.id)},
            enabled=True,
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(handled, ["job-1"])
        self.assertEqual(service.completed, ["worker-a:job-1"])
