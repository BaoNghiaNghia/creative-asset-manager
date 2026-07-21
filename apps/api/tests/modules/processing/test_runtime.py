from __future__ import annotations

import logging
import signal
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import JobHandlerResult, WorkerDependencies
from app.modules.processing.bootstrap import build_worker_runtime, run_worker
from app.modules.processing.health import WorkerHealthState
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.registry import WORKER_HANDLER_TYPES, build_handler_registry
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.modules.processing.service import ProcessingJobService


class WorkerRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "worker.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def enqueue(self, key: str, job_type: str = "asset_store") -> str:
        with self.sessions() as session:
            job = ProcessingJobService(ProcessingRepository(session)).enqueue_job(
                tenant_id="tenant-a",
                job_type=job_type,
                entity_type="asset",
                entity_id=key,
                idempotency_key=key,
                payload={"asset_id": key},
            )
            return job.id

    def runtime(
        self,
        handler,
        *,
        drain: float = 0.2,
        heartbeat: float = 0.02,
        lease: float = 0.2,
        worker_id: str = "worker-a",
        closers=(),
    ) -> WorkerRuntime:
        return WorkerRuntime(
            config=WorkerRuntimeConfig(
                worker_id=worker_id,
                enabled=True,
                lease_seconds=lease,
                heartbeat_seconds=heartbeat,
                idle_poll_seconds=0.01,
                drain_timeout_seconds=drain,
            ),
            dependencies=WorkerDependencies(self.sessions, closers=closers),
            registry=build_handler_registry((("asset_store", handler),)),
            logger=logging.getLogger(f"test.{worker_id}"),
        )

    def job(self, job_id: str) -> ProcessingJobModel:
        with self.sessions() as session:
            return session.get(ProcessingJobModel, job_id)

    def test_registry_explicitly_registers_every_worker_type(self) -> None:
        registry = build_handler_registry()
        self.assertEqual(set(registry.job_types), set(WORKER_HANDLER_TYPES))

    def test_unknown_job_type_is_non_retryable(self) -> None:
        with self.sessions.begin() as session:
            model = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="future_job",
                entity_type="asset",
                entity_id="asset-1",
                idempotency_key="future-1",
                payload_json={},
                next_attempt_at=datetime.now(timezone.utc),
            )
            session.add(model)
        runtime = WorkerRuntime(
            config=WorkerRuntimeConfig(
                worker_id="worker-a",
                enabled=True,
                lease_seconds=1,
                heartbeat_seconds=0.1,
                idle_poll_seconds=0.01,
                drain_timeout_seconds=0.1,
            ),
            dependencies=WorkerDependencies(self.sessions),
            registry=build_handler_registry(),
        )
        self.assertTrue(runtime.run_once())
        stored = self.job(model.id)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.last_error_code, "unsupported_handler")

    def test_success_retryable_and_non_retryable_results(self) -> None:
        cases = (
            ("success", JobHandlerResult.completed(), "completed"),
            ("retry", JobHandlerResult.retryable("temporary", "retry"), "retry"),
            ("failure", JobHandlerResult.non_retryable("invalid", "stop"), "failed"),
        )
        for key, result, status in cases:
            with self.subTest(result=result.outcome):
                job_id = self.enqueue(key)
                runtime = self.runtime(lambda _context, value=result: value)
                self.assertTrue(runtime.run_once())
                self.assertEqual(self.job(job_id).status, status)

    def test_heartbeat_extends_the_lease(self) -> None:
        job_id = self.enqueue("heartbeat")
        observed: list[datetime] = []

        def handler(_context):
            time.sleep(0.07)
            with self.sessions() as session:
                observed.append(session.get(ProcessingJobModel, job_id).lease_expires_at)
            return JobHandlerResult.completed()

        runtime = self.runtime(handler, heartbeat=0.02, lease=0.08)
        runtime.run_once()
        self.assertTrue(observed)
        self.assertGreater(observed[0], datetime.now(timezone.utc).replace(tzinfo=None))

    def test_lost_lease_prevents_completion(self) -> None:
        job_id = self.enqueue("lease-loss")
        started = threading.Event()

        def handler(context):
            started.set()
            context.cancellation_requested.wait(1)
            return JobHandlerResult.completed()

        runtime = self.runtime(handler, heartbeat=0.02, lease=0.2)
        thread = threading.Thread(target=runtime.run_once)
        thread.start()
        self.assertTrue(started.wait(1))
        with self.sessions.begin() as session:
            model = session.get(ProcessingJobModel, job_id)
            model.claimed_by = "worker-b"
        thread.join(2)
        stored = self.job(job_id)
        self.assertEqual(stored.status, "processing")
        self.assertEqual(stored.claimed_by, "worker-b")

    def test_two_runtimes_do_not_process_same_job(self) -> None:
        job_id = self.enqueue("concurrent")
        calls: list[str] = []
        barrier = threading.Barrier(2)

        def make_handler(worker_id):
            def handler(_context):
                calls.append(worker_id)
                return JobHandlerResult.completed()
            return handler

        runtimes = (
            self.runtime(make_handler("a"), worker_id="worker-a"),
            self.runtime(make_handler("b"), worker_id="worker-b"),
        )

        def run(runtime):
            barrier.wait()
            runtime.run_once()

        threads = [threading.Thread(target=run, args=(runtime,)) for runtime in runtimes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.job(job_id).status, "completed")

    def test_shutdown_stops_new_claims_and_allows_active_job_to_finish(self) -> None:
        first = self.enqueue("drain-first")
        second = self.enqueue("drain-second")
        started = threading.Event()
        finish = threading.Event()

        def handler(_context):
            started.set()
            finish.wait(1)
            return JobHandlerResult.completed()

        runtime = self.runtime(handler, drain=0.5)
        thread = threading.Thread(target=runtime.run_forever)
        thread.start()
        self.assertTrue(started.wait(1))
        runtime.request_shutdown()
        self.assertFalse(runtime.health.snapshot().ready)
        self.assertTrue(runtime.health.snapshot().draining)
        finish.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.job(first).status, "completed")
        self.assertEqual(self.job(second).status, "pending")

    def test_drain_timeout_leaves_job_recoverable(self) -> None:
        job_id = self.enqueue("timeout")
        started = threading.Event()
        release = threading.Event()

        def handler(_context):
            started.set()
            release.wait(2)
            return JobHandlerResult.completed()

        runtime = self.runtime(handler, drain=0.02, heartbeat=0.01, lease=0.08)
        thread = threading.Thread(target=runtime.run_forever)
        thread.start()
        self.assertTrue(started.wait(1))
        runtime.request_shutdown()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.job(job_id).status, "processing")
        time.sleep(0.1)
        with self.sessions() as session:
            recovered = ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id="worker-b", lease_seconds=1
            )
        self.assertEqual(recovered.id, job_id)
        release.set()

    def test_readiness_transitions_and_clean_shutdown(self) -> None:
        closed: list[str] = []
        runtime = self.runtime(
            lambda _context: JobHandlerResult.completed(),
            closers=(lambda: closed.append("closed"),),
        )
        self.assertFalse(runtime.health.snapshot().ready)
        runtime.start()
        self.assertTrue(runtime.health.snapshot().ready)
        runtime.request_shutdown()
        self.assertFalse(runtime.health.snapshot().ready)
        runtime.close()
        runtime.close()
        self.assertFalse(runtime.health.snapshot().live)
        self.assertEqual(closed, ["closed"])


class WorkerHealthServerTest(unittest.TestCase):
    def test_health_endpoint_exposes_safe_runtime_state(self) -> None:
        import http.client

        from app.modules.processing.health import WorkerHealthServer

        state = WorkerHealthState("worker-health")
        state.startup_complete(enabled=True, database_available=True)
        server = WorkerHealthServer(state, "127.0.0.1", 0)
        server.start()
        try:
            connection = http.client.HTTPConnection(*server.address, timeout=2)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('"worker_id":"worker-health"', body)
            self.assertNotIn("credential", body)
            connection.close()

            state.start_draining()
            connection = http.client.HTTPConnection(*server.address, timeout=2)
            connection.request("GET", "/ready")
            response = connection.getresponse()
            self.assertEqual(response.status, 503)
            connection.close()
        finally:
            server.close()


class WorkerBootstrapTest(unittest.TestCase):
    def test_startup_database_failure_returns_nonzero(self) -> None:
        class BrokenSession:
            def __enter__(self):
                raise RuntimeError("database unavailable")

            def __exit__(self, *_args):
                return False

        settings = Settings(PROCESSING_JOBS_ENABLED=False)
        with patch(
            "app.modules.processing.bootstrap.configure_worker_logging",
            return_value=logging.getLogger("test.startup"),
        ):
            result = run_worker(
                settings,
                session_factory=BrokenSession,
                install_signal_handlers=False,
            )
        self.assertEqual(result, 1)

    def test_sigterm_requests_shutdown(self) -> None:
        callbacks = {}
        fake_health = WorkerHealthState("worker-a")
        fake_runtime = SimpleNamespace(
            config=SimpleNamespace(
                worker_id="worker-a",
                enabled=True,
                lease_seconds=60,
                heartbeat_seconds=15,
                drain_timeout_seconds=30,
            ),
            health=fake_health,
            registry=SimpleNamespace(job_types=()),
            shutdown_calls=0,
            close=lambda: None,
        )

        def request_shutdown():
            fake_runtime.shutdown_calls += 1

        def run_forever():
            callbacks[signal.SIGTERM](signal.SIGTERM, None)

        fake_runtime.request_shutdown = request_shutdown
        fake_runtime.run_forever = run_forever

        class FakeServer:
            def __init__(self, *_args):
                pass

            def start(self):
                pass

            def close(self):
                pass

        with (
            patch(
                "app.modules.processing.bootstrap.build_worker_runtime",
                return_value=fake_runtime,
            ),
            patch(
                "app.modules.processing.bootstrap.signal.signal",
                side_effect=lambda sig, callback: callbacks.__setitem__(sig, callback),
            ),
        ):
            result = run_worker(
                Settings(PROCESSING_JOBS_ENABLED=True),
                health_server_factory=FakeServer,
            )
        self.assertEqual(result, 0)
        self.assertEqual(fake_runtime.shutdown_calls, 1)

    def test_runtime_build_initializes_provider_boundaries(self) -> None:
        directory = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{Path(directory.name) / 'bootstrap.db'}")
        sessions = sessionmaker(bind=engine)
        runtime = build_worker_runtime(
            Settings(PROCESSING_JOBS_ENABLED=False),
            session_factory=sessions,
        )
        self.assertIsNotNone(runtime.dependencies.source_provider_factory)
        self.assertIsNotNone(runtime.dependencies.storage_provider)
        self.assertIsNotNone(runtime.dependencies.ai_provider_registry)
        self.assertEqual(runtime.dependencies.ai_provider_registry.list_capabilities(), ())
        runtime.close()
        engine.dispose()
        directory.cleanup()


    def test_runtime_registers_configured_gemini(self) -> None:
        directory = tempfile.TemporaryDirectory()
        engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'gemini-bootstrap.db'}"
        )
        sessions = sessionmaker(bind=engine)
        runtime = build_worker_runtime(
            Settings(PROCESSING_JOBS_ENABLED=False, GEMINI_API_KEY="test-only"),
            session_factory=sessions,
        )
        self.assertTrue(runtime.dependencies.ai_provider_registry.has("gemini"))
        runtime.close()
        engine.dispose()
        directory.cleanup()



    def test_runtime_does_not_register_disabled_openai(self) -> None:
        directory = tempfile.TemporaryDirectory()
        engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'openai-disabled.db'}"
        )
        sessions = sessionmaker(bind=engine)
        runtime = build_worker_runtime(
            Settings(
                PROCESSING_JOBS_ENABLED=False,
                OPENAI_API_KEY="test-only",
            ),
            session_factory=sessions,
        )
        self.assertFalse(runtime.dependencies.ai_provider_registry.has("openai"))
        runtime.close()
        engine.dispose()
        directory.cleanup()

    def test_runtime_registers_enabled_openai(self) -> None:
        directory = tempfile.TemporaryDirectory()
        engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'openai-enabled.db'}"
        )
        sessions = sessionmaker(bind=engine)
        runtime = build_worker_runtime(
            Settings(
                PROCESSING_JOBS_ENABLED=False,
                OPENAI_AI_ENABLED=True,
                OPENAI_API_KEY="test-only",
                OPENAI_DEFAULT_MODEL="openai-test",
                OPENAI_ALLOWED_MODELS="openai-test",
            ),
            session_factory=sessions,
        )
        provider = runtime.dependencies.ai_provider_registry.require("openai")
        self.assertEqual(provider.default_model, "openai-test")
        self.assertTrue(provider.supports_single)
        self.assertFalse(provider.supports_batch)
        runtime.close()
        engine.dispose()
        directory.cleanup()
if __name__ == "__main__":
    unittest.main()
