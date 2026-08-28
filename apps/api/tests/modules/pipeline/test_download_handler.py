import logging
import unittest
from threading import Event

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import (
    ClaimedJob,
    JobHandlerContext,
    JobOutcome,
    WorkerDependencies,
)
from app.domain.providers.contracts import StorageProviderError
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.handlers import (
    AssetStoreJobHandler,
    SourceAssetDownloadJobHandler,
)
from app.modules.pipeline.mime_types import SourceContentTooLarge
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel


class FailIfCalledStage:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.called = False

    async def execute(self, *, tenant_id, pipeline):
        self.called = True
        if self.error is not None:
            raise self.error
        raise AssertionError("stage should not be called")


class SourceAssetDownloadJobHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.settings = Settings(
            UNIFIED_ASSET_INGESTION_ENABLED=True,
            CONTENT_DEDUP_ENABLED=True,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _context(self, mime_type: str, stage: FailIfCalledStage) -> JobHandlerContext:
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id="tenant-a",
                source_key="drive",
                source_type="google_drive",
            )
            source_asset = assets.upsert_source_asset(
                tenant_id="tenant-a",
                external_source_id=source.id,
                external_asset_id=mime_type,
                filename="item",
                mime_type=mime_type,
            )
            session.commit()
            source_asset_id = source_asset.id
        return JobHandlerContext(
            job=ClaimedJob(
                id="job",
                tenant_id="tenant-a",
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=source_asset_id,
                payload={"source_asset_id": source_asset_id},
                attempt_count=0,
                lease_owner="worker",
            ),
            dependencies=WorkerDependencies(
                session_factory=self.sessions,
                resources={"pipeline_download_stage": stage},
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger(__name__), {}),
        )

    def test_missing_download_stage_is_retryable_not_terminal(self) -> None:
        context = self._context("image/jpeg", FailIfCalledStage())
        context.dependencies.resources.clear()

        result = SourceAssetDownloadJobHandler(self.settings)(context)

        self.assertEqual(result.outcome, JobOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "download_stage_unconfigured")
        with self.sessions() as session:
            pipeline = session.scalar(select(AssetPipelineModel))
            self.assertEqual(pipeline.last_error_code, "download_stage_unconfigured")
            self.assertTrue(pipeline.failure_retryable)

    def test_unsupported_google_drive_mime_is_terminal_before_download(self) -> None:
        stage = FailIfCalledStage()
        result = SourceAssetDownloadJobHandler(self.settings)(
            self._context("video/mp4", stage)
        )

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "unsupported_source_mime_type")
        self.assertFalse(stage.called)
        with self.sessions() as session:
            pipeline = session.scalar(select(AssetPipelineModel))
            self.assertEqual(pipeline.last_error_code, "unsupported_source_mime_type")
            self.assertFalse(pipeline.failure_retryable)

    def test_permanent_storage_error_continues_with_marked_source_analysis(self) -> None:
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id="tenant-a",
                source_key="drive-fallback",
                source_type="google_drive",
            )
            source_asset = assets.upsert_source_asset(
                tenant_id="tenant-a",
                external_source_id=source.id,
                external_asset_id="source-fallback",
                filename="source.avif",
                mime_type="image/avif",
            )
            asset = AssetModel(
                tenant_id="tenant-a",
                content_hash="b" * 64,
                mime_type="image/avif",
                size_bytes=100,
            )
            session.add(asset)
            session.flush()
            AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="auto",
                profile_version="1",
                prompt_template="Analyze {{ asset }}",
            )
            pipeline = AssetPipelineModel(
                tenant_id="tenant-a",
                correlation_id="storage-source-fallback",
                origin_type="source_asset",
                origin_id=source_asset.id,
                source_asset_id=source_asset.id,
                asset_id=asset.id,
                content_hash=asset.content_hash,
                state="storage_pending",
            )
            session.add(pipeline)
            session.commit()
            pipeline_id = pipeline.id
        context = JobHandlerContext(
            job=ClaimedJob(
                id="storage-fallback-job",
                tenant_id="tenant-a",
                job_type="asset_store",
                entity_type="asset_pipeline",
                entity_id=pipeline_id,
                payload={"pipeline_id": pipeline_id},
                attempt_count=0,
                lease_owner="worker",
            ),
            dependencies=WorkerDependencies(
                session_factory=self.sessions,
                resources={
                    "pipeline_storage_stage": FailIfCalledStage(
                        StorageProviderError(
                            "credentials rejected",
                            retryable=False,
                            code="managed_storage_unauthorized",
                        )
                    )
                },
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger(__name__), {}),
        )
        settings = Settings(
            MANAGED_ASSET_STORAGE_ENABLED=True,
            AI_ANALYSIS_SOURCE_FALLBACK_ENABLED=True,
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_AUTO_ANALYZE_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            GEMINI_API_KEY="test-only",
        )

        result = AssetStoreJobHandler(settings)(context)

        self.assertEqual(result.outcome, JobOutcome.COMPLETED)
        with self.sessions() as session:
            pipeline = session.get(AssetPipelineModel, pipeline_id)
            self.assertEqual(pipeline.state, "analysis_pending")
            job = session.scalar(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "asset_analyze"
                )
            )
            self.assertIsNotNone(job)
            self.assertEqual(
                job.payload_json["analysis_content_source"], "source_asset"
            )

    def test_permanent_storage_provider_error_is_not_retried(self) -> None:
        with self.sessions() as session:
            pipeline = AssetPipelineModel(
                tenant_id="tenant-a",
                correlation_id="storage-auth-test",
                origin_type="ingestion_item",
                origin_id="storage-auth-test",
                state="storage_pending",
            )
            session.add(pipeline)
            session.commit()
            pipeline_id = pipeline.id
        context = JobHandlerContext(
            job=ClaimedJob(
                id="storage-job",
                tenant_id="tenant-a",
                job_type="asset_store",
                entity_type="asset_pipeline",
                entity_id=pipeline_id,
                payload={"pipeline_id": pipeline_id},
                attempt_count=0,
                lease_owner="worker",
            ),
            dependencies=WorkerDependencies(
                session_factory=self.sessions,
                resources={
                    "pipeline_storage_stage": FailIfCalledStage(
                        StorageProviderError(
                            "credentials rejected",
                            retryable=False,
                            code="managed_storage_unauthorized",
                        )
                    )
                },
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger(__name__), {}),
        )

        result = AssetStoreJobHandler(
            Settings(MANAGED_ASSET_STORAGE_ENABLED=True)
        )(context)

        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "managed_storage_unauthorized")
        with self.sessions() as session:
            pipeline = session.get(AssetPipelineModel, pipeline_id)
            self.assertEqual(pipeline.state, "storage_failed")
            self.assertEqual(
                pipeline.last_error_code, "managed_storage_unauthorized"
            )
            self.assertFalse(pipeline.failure_retryable)

    def test_oversized_image_is_terminal_with_stable_error_code(self) -> None:
        stage = FailIfCalledStage(SourceContentTooLarge("too large"))
        result = SourceAssetDownloadJobHandler(self.settings)(
            self._context("image/jpeg", stage)
        )

        self.assertTrue(stage.called)
        self.assertEqual(result.outcome, JobOutcome.NON_RETRYABLE_FAILURE)
        self.assertEqual(result.error_code, "source_content_too_large")
        with self.sessions() as session:
            pipeline = session.scalar(select(AssetPipelineModel))
            self.assertEqual(pipeline.last_error_code, "source_content_too_large")
            self.assertFalse(pipeline.failure_retryable)


if __name__ == "__main__":
    unittest.main()
