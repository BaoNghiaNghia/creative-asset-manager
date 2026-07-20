import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.repository import AssetPipelineRepository
from app.modules.pipeline.service import AssetPipelineService
from app.modules.pipeline.state import InvalidPipelineTransition, PipelineState, validate_transition
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


class PipelineStateTest(unittest.TestCase):
    def test_happy_path_and_recoverable_stage_failure(self):
        states = [
            "discovered", "download_pending", "downloading", "downloaded",
            "storage_pending", "stored", "analysis_pending", "analyzing",
            "metadata_ready", "projection_ready", "search_pending", "indexed",
            "sidecar_pending", "completed",
        ]
        for current, target in zip(states, states[1:]):
            validate_transition(current, target)
        validate_transition("downloading", "download_failed")
        validate_transition("download_failed", "download_pending")

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(InvalidPipelineTransition):
            validate_transition("discovered", "indexed")


class PipelineRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _source_asset(self, session):
        assets = AssetRegistryRepository(session)
        source = assets.upsert_external_source(
            tenant_id="tenant-a", source_key="drive", source_type="google_drive"
        )
        return assets.upsert_source_asset(
            tenant_id="tenant-a", external_source_id=source.id,
            external_asset_id="file-1", filename="one.png",
        )

    def test_discovery_and_job_are_atomic_and_idempotent(self):
        with self.sessions() as session:
            source_asset = self._source_asset(session)
            service = AssetPipelineService(
                AssetPipelineRepository(session), ProcessingRepository(session)
            )
            first = service.discover_and_enqueue(
                tenant_id="tenant-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id,
            )
            second = service.discover_and_enqueue(
                tenant_id="tenant-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id,
            )
            session.commit()
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.state, PipelineState.DOWNLOAD_PENDING.value)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 1)
            job = session.scalar(select(ProcessingJobModel))
            self.assertEqual(job.payload_json["correlation_id"], first.correlation_id)
            self.assertNotIn("download_url", job.payload_json)

    def test_crash_rolls_back_transition_and_next_job(self):
        with self.sessions() as session:
            source_asset = self._source_asset(session)
            AssetPipelineService(
                AssetPipelineRepository(session), ProcessingRepository(session)
            ).discover_and_enqueue(
                tenant_id="tenant-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id,
            )
            session.rollback()
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetPipelineModel)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 0)

    def test_stage_failure_is_observable_and_retryable(self):
        with self.sessions() as session:
            source_asset = self._source_asset(session)
            repository = AssetPipelineRepository(session)
            pipeline = repository.get_or_create(
                tenant_id="tenant-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id,
            )
            repository.transition(pipeline, PipelineState.DOWNLOAD_PENDING)
            repository.transition(pipeline, PipelineState.DOWNLOADING)
            repository.record_failure(
                pipeline, "download", error_code="timeout",
                error_message="provider timed out", retryable=True,
            )
            session.commit()
            self.assertEqual(pipeline.state, "download_failed")
            self.assertTrue(pipeline.failure_retryable)
            repository.transition(pipeline, PipelineState.DOWNLOAD_PENDING)
            session.commit()
            self.assertEqual(pipeline.state, "download_pending")
