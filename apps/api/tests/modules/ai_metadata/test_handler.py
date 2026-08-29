import logging
import unittest
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import (
    ClaimedJob,
    DeferredJobOutcome,
    JobHandlerContext,
    JobOutcome,
    WorkerDependencies,
)
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.service import AiAnalysisOutcome
from app.modules.assets.model import AssetModel
from app.modules.storage.model import AssetStorageObjectModel


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

    def test_temporary_gemini_pool_outcome_is_deferred(self):
        analysis_id = self._analysis("gemini")
        registry = AiProviderRegistry()
        registry.register("gemini", FakeProvider("gemini"))
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=1)

        with patch("app.modules.ai_metadata.handler.AiAnalysisService") as service_class:
            service_class.return_value.analyze = AsyncMock(
                return_value=AiAnalysisOutcome(
                    "deferred",
                    "gemini_quota_deferred",
                    "Gemini capacity is temporarily unavailable.",
                    retry_at=retry_at,
                    metadata={"attempted_models": ["gemini-first"]},
                )
            )
            result = AssetAnalyzeJobHandler(Settings())(
                self._context(analysis_id, registry)
            )

        self.assertIsInstance(result, DeferredJobOutcome)
        self.assertEqual(result.reason_code, "gemini_quota_deferred")
        self.assertEqual(result.retry_at, retry_at)
        self.assertEqual(result.metadata["attempted_models"], ["gemini-first"])

    def test_missing_persisted_provider_is_non_retryable(self):
        analysis_id = self._analysis("openai")
        registry = AiProviderRegistry()
        registry.register("gemini", FakeProvider("gemini"))

        result = AssetAnalyzeJobHandler(Settings())(
            self._context(analysis_id, registry)
        )

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "ai_provider_unavailable")

    def test_pending_managed_storage_is_deferred_without_invoking_ai(self):
        analysis_id = self._analysis("gemini")
        registry = AiProviderRegistry()
        registry.register("gemini", FakeProvider("gemini"))
        with self.sessions() as session:
            session.add(AssetStorageObjectModel(
                tenant_id="tenant-a",
                asset_id=self.asset_id,
                content_hash="a" * 64,
                storage_provider="google_drive_managed",
                status="uploading",
            ))
            session.commit()

        with patch("app.modules.ai_metadata.handler.AiAnalysisService") as service_class:
            result = AssetAnalyzeJobHandler(Settings(MANAGED_ASSET_STORAGE_ENABLED=True))(
                self._context(analysis_id, registry)
            )

        self.assertIsInstance(result, DeferredJobOutcome)
        self.assertEqual(result.reason_code, "managed_asset_storage_pending")
        service_class.assert_not_called()
        with self.sessions() as session:
            analysis = AiMetadataRepository(session).get_analysis(analysis_id)
            self.assertEqual(analysis.attempt_count, 0)
            self.assertEqual(analysis.status, "pending")

    def test_failed_managed_storage_is_reported_without_ai_retries(self):
        analysis_id = self._analysis("gemini")
        registry = AiProviderRegistry()
        registry.register("gemini", FakeProvider("gemini"))
        with self.sessions() as session:
            session.add(AssetStorageObjectModel(
                tenant_id="tenant-a",
                asset_id=self.asset_id,
                content_hash="a" * 64,
                storage_provider="google_drive_managed",
                status="failed",
                attempt_count=5,
            ))
            session.commit()

        result = AssetAnalyzeJobHandler(Settings(MANAGED_ASSET_STORAGE_ENABLED=True))(
            self._context(analysis_id, registry)
        )

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "managed_asset_storage_failed")


if __name__ == "__main__":
    unittest.main()
