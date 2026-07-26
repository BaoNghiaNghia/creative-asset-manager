from __future__ import annotations

import io
import logging
import tempfile
import threading
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import WorkerDependencies
from app.domain.providers.contracts import (
    AiMetadataAnalysisResult,
    AiProviderError,
    StoredAssetReadStream,
)
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_governance.model import AiCostRateModel, AiUsageRecordModel
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.registry import build_handler_registry
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.modules.processing.service import ProcessingJobService
from app.modules.storage.model import AssetStorageObjectModel
from app.providers.ai.gemini import (
    GeminiModelUnavailable,
    GeminiPoolTemporarilyUnavailable,
)


MODEL_POOL = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)


@dataclass
class FakeClock:
    now: datetime

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeStorage:
    def __init__(self, content: bytes):
        self.content = content

    async def open_asset(self, _input):
        async def body():
            yield self.content

        async def close():
            return None

        return StoredAssetReadStream(
            body=body(), close=close, content_type="image/png"
        )


class FakeGeminiPool:
    provider_name = "gemini"
    default_model = MODEL_POOL[0]
    supports_single = True
    supports_batch = False

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.available = False
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_success = False

    async def analyze_single(self, _input):
        self.calls += 1
        if not self.available:
            retry_at = self.clock.now + timedelta(minutes=5)
            raise GeminiPoolTemporarilyUnavailable(
                attempted_models=list(MODEL_POOL),
                reasons_by_model={
                    model: GeminiModelUnavailable(
                        model=model,
                        reason="rpm_exhausted",
                        available_at=retry_at,
                    )
                    for model in MODEL_POOL
                },
                earliest_retry_at=retry_at,
            )
        self.started.set()
        if self.block_success:
            self.release.wait(2)
        return AiMetadataAnalysisResult(
            metadata={"subject": "cat"},
            provider="gemini",
            model=self.default_model,
            provider_request_id="fake-request",
            usage={"total": 1},
            provider_metadata={"finish_reason": "STOP"},
        )


class PermanentGeminiProvider(FakeGeminiPool):
    async def analyze_single(self, _input):
        self.calls += 1
        raise AiProviderError(
            "Gemini API key is invalid.",
            code="gemini_http_error",
            retryable=False,
            status_code=401,
        )


class Http429GeminiProvider(FakeGeminiPool):
    async def analyze_single(self, _input):
        self.calls += 1
        raise AiProviderError(
            "Gemini rate limit was reached.",
            code="gemini_rate_limited",
            retryable=True,
            status_code=429,
            details={"retry_after_seconds": 45},
        )


class GeminiDeferredWorkerE2ETest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = FakeClock(datetime(2040, 1, 1, 10, tzinfo=timezone.utc))
        path = Path(self.directory.name) / "gemini-deferred.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        image = io.BytesIO()
        Image.new("RGB", (20, 10), "blue").save(image, format="PNG")
        self.storage = FakeStorage(image.getvalue())
        self.settings = Settings(
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            GEMINI_API_KEY="test-only",
            GEMINI_MODEL=MODEL_POOL[0],
            GEMINI_ALLOWED_MODELS=",".join(MODEL_POOL),
            AI_ANALYSIS_LEASE_SECONDS=60,
        )
        with self.sessions() as session:
            asset = AssetModel(
                tenant_id="tenant-a",
                content_hash="a" * 64,
                mime_type="image/png",
                size_bytes=len(image.getvalue()),
            )
            session.add(asset)
            session.flush()
            profile = AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze {{ asset }}",
                optional_json_schema={
                    "type": "object",
                    "properties": {"subject": {"type": "string"}},
                    "required": ["subject"],
                },
            )
            analysis = AiMetadataRepository(session).create_analysis(
                tenant_id="tenant-a",
                asset_id=asset.id,
                metadata_profile_id=profile.id,
                prompt_version="test",
                pipeline_version="single-v1",
                ai_provider="gemini",
                ai_model=MODEL_POOL[0],
            )
            session.add(AiCostRateModel(
                provider="gemini",
                model=MODEL_POOL[0],
                processing_mode="single",
                effective_at=datetime.now(timezone.utc) - timedelta(days=1),
                input_unit_cost=0,
                output_unit_cost=0,
                media_unit_cost=0,
                currency="USD",
            ))
            session.add(AssetStorageObjectModel(
                tenant_id="tenant-a",
                asset_id=asset.id,
                content_hash=asset.content_hash,
                storage_provider="google_drive_managed",
                status="stored",
                remote_file_id="stored-image",
                remote_folder_id="folder",
            ))
            session.commit()
            self.asset_id = asset.id
            self.analysis_id = analysis.id

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def _enqueue(self) -> str:
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            first = service.enqueue_job(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id=self.analysis_id,
                idempotency_key=f"analysis:{self.analysis_id}:gemini",
                payload={"analysis_id": self.analysis_id},
                provider_key="gemini",
                provider_scope="ai",
                max_attempts=3,
            )
            duplicate = service.enqueue_job(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id=self.analysis_id,
                idempotency_key=f"analysis:{self.analysis_id}:gemini",
                payload={"analysis_id": self.analysis_id},
                provider_key="gemini",
                provider_scope="ai",
                max_attempts=3,
            )
            self.assertEqual(first.id, duplicate.id)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(ProcessingJobModel).where(
                        ProcessingJobModel.tenant_id == "tenant-a"
                    )
                ),
                1,
            )
            return first.id

    def _runtime(self, provider, worker_id: str) -> WorkerRuntime:
        registry = AiProviderRegistry()
        registry.register("gemini", provider)
        return WorkerRuntime(
            config=WorkerRuntimeConfig(
                worker_id=worker_id,
                enabled=True,
                lease_seconds=60,
                heartbeat_seconds=10,
                idle_poll_seconds=0.01,
                drain_timeout_seconds=1,
            ),
            dependencies=WorkerDependencies(
                session_factory=self.sessions,
                storage_provider=self.storage,
                ai_provider_registry=registry,
            ),
            registry=build_handler_registry((
                ("asset_analyze", AssetAnalyzeJobHandler(self.settings)),
            )),
            logger=logging.getLogger(f"test.gemini-deferred.{worker_id}"),
        )

    def _job(self, job_id: str) -> ProcessingJobModel:
        with self.sessions() as session:
            return session.get(ProcessingJobModel, job_id)

    def test_deferred_job_restarts_then_completes_once_after_quota_recovers(self):
        from unittest.mock import patch

        job_id = self._enqueue()
        provider = FakeGeminiPool(self.clock)
        first_worker = self._runtime(provider, "worker-first")
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertTrue(first_worker.run_once())

        deferred = self._job(job_id)
        self.assertEqual(deferred.status, "pending")
        self.assertEqual(deferred.attempt_count, 0)
        self.assertGreater(deferred.next_attempt_at.replace(tzinfo=timezone.utc), self.clock.now)
        self.assertEqual(deferred.last_error_code, "gemini_quota_deferred")
        with self.sessions() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(analysis.status, "pending")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(AiUsageRecordModel)),
                0,
            )

        restarted_worker = self._runtime(provider, "worker-restarted")
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertFalse(restarted_worker.run_once())
            self.assertFalse(self._runtime(provider, "worker-other").run_once())

        self.clock.advance(timedelta(minutes=5, seconds=1))
        provider.available = True
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertTrue(restarted_worker.run_once())

        completed = self._job(job_id)
        self.assertEqual(completed.status, "completed")
        with self.sessions() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(analysis.status, "completed")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(AiUsageRecordModel)),
                1,
            )
        self.assertEqual(provider.calls, 2)

    def test_two_workers_cannot_claim_the_recovered_deferred_job(self):
        from unittest.mock import patch

        job_id = self._enqueue()
        provider = FakeGeminiPool(self.clock)
        first = self._runtime(provider, "worker-first")
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertTrue(first.run_once())
        self.clock.advance(timedelta(minutes=5, seconds=1))
        provider.available = True
        provider.block_success = True
        winner = self._runtime(provider, "worker-winner")
        loser = self._runtime(provider, "worker-loser")
        outcome: list[bool] = []

        def run_winner():
            with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
                outcome.append(winner.run_once())

        thread = threading.Thread(target=run_winner)
        thread.start()
        self.assertTrue(provider.started.wait(1))
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertFalse(loser.run_once())
        provider.release.set()
        thread.join(2)

        self.assertEqual(outcome, [True])
        self.assertEqual(self._job(job_id).status, "completed")
        self.assertEqual(provider.calls, 2)

    def test_http_429_requeues_without_sleeping(self):
        job_id = self._enqueue()
        provider = Http429GeminiProvider(self.clock)
        started = datetime.now(timezone.utc)
        self.assertTrue(self._runtime(provider, "worker-429").run_once())

        job = self._job(job_id)
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempt_count, 0)
        self.assertEqual(job.last_error_code, "ai_provider_rate_limited")
        self.assertGreaterEqual(
            job.next_attempt_at.replace(tzinfo=timezone.utc),
            started + timedelta(seconds=44),
        )
        self.assertFalse(self._runtime(provider, "worker-429-next").run_once())

    def test_permanent_gemini_failure_is_failed(self):
        from unittest.mock import patch

        job_id = self._enqueue()
        worker = self._runtime(PermanentGeminiProvider(self.clock), "worker-permanent")
        with patch("app.modules.processing.repository.utcnow", side_effect=lambda: self.clock.now):
            self.assertTrue(worker.run_once())

        job = self._job(job_id)
        self.assertEqual(job.status, "failed")
        with self.sessions() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(analysis.status, "failed")


if __name__ == "__main__":
    unittest.main()
