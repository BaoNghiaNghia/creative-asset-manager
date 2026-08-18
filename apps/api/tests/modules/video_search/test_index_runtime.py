from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
from app.modules.assets.model import SourceAssetModel
from app.modules.processing.bootstrap import build_worker_runtime
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.video_search.index_enqueue import enqueue_video_search_index_job
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
)


class VideoSearchIndexRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'video-index-runtime.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            VIDEO_SEARCH_ENABLED=True,
            ELASTICSEARCH_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://test-elasticsearch.invalid",
            WORKER_ID="video-index-runtime-test",
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _create_completed_run_and_job(
        self, *, search_v2_enabled: bool = True
    ) -> tuple[str, str]:
        with self.sessions() as session:
            source = SourceAssetModel(
                id="asset-runtime",
                tenant_id="tenant-a",
                external_source_id="source-runtime",
                external_asset_id="external-runtime",
                filename="clip.mp4",
                mime_type="video/mp4",
                source_metadata={},
            )
            run = VideoAnalysisRunModel(
                id="run-runtime",
                tenant_id="tenant-a",
                source_asset_id=source.id,
                source_fingerprint="f" * 64,
                video_metadata_profile_id="profile-runtime",
                metadata_profile="video",
                metadata_profile_version="v1",
                prompt_version="prompt-v1",
                analysis_version="analysis-v1",
                ai_provider="gemini",
                ai_model="model-a",
                idempotency_key="r" * 64,
                status="completed",
                duration_ms=1000,
                chunk_seconds=1000,
                total_chunks=1,
                completed_chunks=1,
            )
            chunk = VideoAnalysisChunkModel(
                id="chunk-runtime",
                tenant_id="tenant-a",
                run_id=run.id,
                chunk_index=0,
                source_start_ms=0,
                source_end_ms=1000,
                status="completed",
                metadata_json={"segments": [{"start_ms": 0, "end_ms": 500}]},
            )
            policy = TenantProcessingPolicyModel(
                tenant_id="tenant-a",
                pipeline_enabled=True,
                search_v2_enabled=search_v2_enabled,
            )
            session.add_all((source, run, chunk, policy))
            session.flush()
            created = enqueue_video_search_index_job(
                tenant_id="tenant-a",
                run=run,
                processing=ProcessingRepository(session),
            )
            self.assertTrue(created)
            job = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == "tenant-a",
                ProcessingJobModel.job_type == "video_search_index",
            ))
            self.assertIsNotNone(job)
            session.commit()
            return run.id, job.id

    def _job(self, job_id: str) -> ProcessingJobModel:
        with self.sessions() as session:
            return session.get(ProcessingJobModel, job_id)

    def _run_once(self, job_id: str, upsert) -> ProcessingJobModel:
        with (
            patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as index_type,
            patch("app.modules.video_search.handler.GeminiVideoClient") as gemini_type,
            patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type,
            patch("app.modules.processing.bootstrap.create_source_provider") as source_provider,
        ):
            index_type.return_value.upsert_video_document = upsert
            runtime = build_worker_runtime(
                self.settings,
                session_factory=self.sessions,
            )
            try:
                self.assertTrue(runtime.run_once())
            finally:
                runtime.close()
            gemini_type.assert_not_called()
            proxy_type.assert_not_called()
            source_provider.assert_not_called()
        return self._job(job_id)

    def test_runtime_does_not_claim_when_tenant_search_policy_is_disabled(self) -> None:
        _run_id, job_id = self._create_completed_run_and_job(
            search_v2_enabled=False
        )
        with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as index_type:
            runtime = build_worker_runtime(
                self.settings,
                session_factory=self.sessions,
            )
            try:
                self.assertFalse(runtime.run_once())
            finally:
                runtime.close()
        index_type.assert_not_called()
        stored = self._job(job_id)
        self.assertEqual(stored.status, "pending")
        self.assertEqual(stored.attempt_count, 0)
        self.assertIsNone(stored.claimed_by)
        self.assertIsNone(stored.lease_expires_at)

    def test_runtime_completes_video_search_index_job_and_releases_lease(self) -> None:
        run_id, job_id = self._create_completed_run_and_job()
        upsert = AsyncMock()

        stored = self._run_once(job_id, upsert)

        self.assertEqual(stored.status, "completed")
        self.assertIsNotNone(stored.completed_at)
        self.assertEqual(stored.attempt_count, 1)
        self.assertIsNone(stored.claimed_by)
        self.assertIsNone(stored.claimed_at)
        self.assertIsNone(stored.lease_expires_at)
        self.assertIsNone(stored.last_error_code)
        self.assertIsNone(stored.last_error_message)
        upsert.assert_awaited_once()

        with self.sessions() as session:
            run = session.get(VideoAnalysisRunModel, run_id)
            chunk = session.get(VideoAnalysisChunkModel, "chunk-runtime")
            self.assertEqual(run.status, "completed")
            self.assertEqual(chunk.status, "completed")
            self.assertEqual(
                chunk.metadata_json,
                {"segments": [{"start_ms": 0, "end_ms": 500}]},
            )

    def test_runtime_fails_terminally_for_deterministic_request_rejection(self) -> None:
        run_id, job_id = self._create_completed_run_and_job()
        upsert = AsyncMock(side_effect=ElasticsearchV3RequestError("mapping rejected", status_code=400))

        stored = self._run_once(job_id, upsert)

        self.assertEqual(stored.status, "failed")
        self.assertIsNotNone(stored.completed_at)
        self.assertEqual(stored.attempt_count, 1)
        self.assertIsNone(stored.claimed_by)
        self.assertIsNone(stored.claimed_at)
        self.assertIsNone(stored.lease_expires_at)
        self.assertEqual(stored.last_error_code, "video_index_elasticsearch_request_rejected")
        upsert.assert_awaited_once()
        with self.sessions() as session:
            self.assertEqual(session.get(VideoAnalysisRunModel, run_id).status, "completed")

    def test_runtime_retries_transient_video_search_index_error_and_releases_lease(self) -> None:
        run_id, job_id = self._create_completed_run_and_job()
        failed_before = datetime.now(timezone.utc)
        upsert = AsyncMock(
            side_effect=ElasticsearchV3RequestError("connection failed")
        )

        stored = self._run_once(job_id, upsert)

        self.assertEqual(stored.status, "retry")
        self.assertIsNotNone(stored.next_attempt_at)
        next_attempt_at = stored.next_attempt_at
        if next_attempt_at.tzinfo is None:
            next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
        self.assertGreater(next_attempt_at, failed_before)
        self.assertIsNone(stored.completed_at)
        self.assertEqual(stored.attempt_count, 1)
        self.assertIsNone(stored.claimed_by)
        self.assertIsNone(stored.claimed_at)
        self.assertIsNone(stored.lease_expires_at)
        self.assertEqual(
            stored.last_error_code,
            "video_index_elasticsearch_unavailable",
        )
        self.assertIn("connection failed", stored.last_error_message)
        upsert.assert_awaited_once()

        with self.sessions() as session:
            run = session.get(VideoAnalysisRunModel, run_id)
            chunk = session.get(VideoAnalysisChunkModel, "chunk-runtime")
            self.assertEqual(run.status, "completed")
            self.assertEqual(chunk.status, "completed")
            self.assertEqual(
                chunk.metadata_json,
                {"segments": [{"start_ms": 0, "end_ms": 500}]},
            )


if __name__ == "__main__":
    unittest.main()
