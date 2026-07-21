import logging
import unittest
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import (
    ClaimedJob,
    JobHandlerContext,
    JobOutcome,
    WorkerDependencies,
)
from app.domain.providers.contracts import AiBatchStatus
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_batch.handlers import AiBatchPollJobHandler
from app.modules.ai_batch.model import AiBatchJobModel
from app.modules.ai_metadata.model import MetadataProfileModel


class FakeBatchProvider:
    supports_single = True
    supports_batch = True
    batch_max_items = 100
    batch_max_request_bytes = 1_000_000

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.default_model = provider_name + "-test"
        self.status_calls = 0

    async def get_batch_status(self, _input):
        self.status_calls += 1
        return AiBatchStatus("running", retry_after_seconds=60)


class AiBatchHandlerProviderTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        with self.sessions() as session:
            profile = MetadataProfileModel(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze",
            )
            session.add(profile)
            session.commit()
            self.profile_id = profile.id

    def tearDown(self):
        self.engine.dispose()

    def _batch(self, provider_name: str):
        with self.sessions() as session:
            batch = AiBatchJobModel(
                tenant_id="tenant-a",
                submission_key=provider_name + "-submission",
                provider=provider_name,
                model=provider_name + "-test",
                metadata_profile_id=self.profile_id,
                metadata_profile="general",
                metadata_profile_version="1",
                prompt_version="p1",
                pipeline_version="batch-v1",
                provider_batch_id="batches/" + provider_name,
                status="submitted",
            )
            session.add(batch)
            session.commit()
            return batch.id

    def _context(self, batch_id: str, registry: AiProviderRegistry):
        return JobHandlerContext(
            job=ClaimedJob(
                id="job-" + batch_id,
                tenant_id="tenant-a",
                job_type="ai_batch_poll",
                entity_type="ai_batch_job",
                entity_id=batch_id,
                payload={"batch_id": batch_id},
                attempt_count=0,
                lease_owner="worker-a",
            ),
            dependencies=WorkerDependencies(
                session_factory=self.sessions,
                storage_provider=object(),
                ai_provider_registry=registry,
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger("test.batch-handler"), {}),
        )

    def test_poll_resolves_batch_provider_without_cross_provider_fallback(self):
        registry = AiProviderRegistry()
        gemini = FakeBatchProvider("gemini")
        openai = FakeBatchProvider("openai")
        registry.register("gemini", gemini)
        registry.register("openai", openai)

        result = AiBatchPollJobHandler(Settings())(
            self._context(self._batch("openai"), registry)
        )

        self.assertEqual(result.outcome, JobOutcome.COMPLETED)
        self.assertEqual(openai.status_calls, 1)
        self.assertEqual(gemini.status_calls, 0)

    def test_unconfigured_batch_provider_is_non_retryable(self):
        registry = AiProviderRegistry()
        gemini = FakeBatchProvider("gemini")
        registry.register("gemini", gemini)

        result = AiBatchPollJobHandler(Settings())(
            self._context(self._batch("openai"), registry)
        )

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "ai_provider_unavailable")
        self.assertEqual(gemini.status_calls, 0)


if __name__ == "__main__":
    unittest.main()
