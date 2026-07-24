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
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.handlers import SourceAssetDownloadJobHandler
from app.modules.pipeline.mime_types import SourceContentTooLarge
from app.modules.pipeline.model import AssetPipelineModel


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
