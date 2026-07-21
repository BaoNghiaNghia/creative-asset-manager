import logging
import unittest
from threading import Event
from unittest.mock import AsyncMock, patch

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
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.service import AiAnalysisOutcome
from app.modules.assets.model import AssetModel


class FakeProvider:
    supports_single = True
    supports_batch = False

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.default_model = provider_name + "-test"


class AssetAnalyzeJobHandlerProviderTest(unittest.TestCase):
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
            asset = AssetModel(
                tenant_id="tenant-a",
                content_hash="a" * 64,
                mime_type="image/png",
            )
            session.add(asset)
            session.flush()
            profile = AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze",
            )
            session.commit()
            self.asset_id = asset.id
            self.profile_id = profile.id

    def tearDown(self):
        self.engine.dispose()

    def _analysis(self, provider_name: str):
        with self.sessions() as session:
            analysis = AiMetadataRepository(session).create_analysis(
                tenant_id="tenant-a",
                asset_id=self.asset_id,
                metadata_profile_id=self.profile_id,
                prompt_version=provider_name,
                pipeline_version="single-v1",
                ai_provider=provider_name,
                ai_model=provider_name + "-test",
                force=True,
            )
            session.commit()
            return analysis.id

    def _context(self, analysis_id: str, registry: AiProviderRegistry):
        return JobHandlerContext(
            job=ClaimedJob(
                id="job-" + analysis_id,
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id=analysis_id,
                payload={"analysis_id": analysis_id},
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
            logger=logging.LoggerAdapter(logging.getLogger("test.ai-handler"), {}),
        )

    def test_selects_exact_persisted_provider(self):
        registry = AiProviderRegistry()
        gemini = FakeProvider("gemini")
        openai = FakeProvider("openai")
        registry.register("gemini", gemini)
        registry.register("openai", openai)

        for provider_name, expected, rejected in (
            ("gemini", gemini, openai),
            ("openai", openai, gemini),
        ):
            with self.subTest(provider=provider_name):
                analysis_id = self._analysis(provider_name)
                with patch(
                    "app.modules.ai_metadata.handler.AiAnalysisService"
                ) as service_class:
                    service_class.return_value.analyze = AsyncMock(
                        return_value=AiAnalysisOutcome("completed")
                    )
                    result = AssetAnalyzeJobHandler(Settings())(
                        self._context(analysis_id, registry)
                    )
                self.assertEqual(result.outcome, JobOutcome.COMPLETED)
                selected = service_class.call_args.kwargs["ai_provider"]
                self.assertIs(selected, expected)
                self.assertIsNot(selected, rejected)

    def test_missing_persisted_provider_is_non_retryable(self):
        analysis_id = self._analysis("openai")
        registry = AiProviderRegistry()
        registry.register("gemini", FakeProvider("gemini"))

        result = AssetAnalyzeJobHandler(Settings())(
            self._context(analysis_id, registry)
        )

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "ai_provider_unavailable")


if __name__ == "__main__":
    unittest.main()
