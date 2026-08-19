from __future__ import annotations

import asyncio
import logging
import os
import threading
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, WorkerDependencies
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.bootstrap import build_worker_runtime
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
from app.modules.video_search.index_enqueue import enqueue_video_search_index_job
from app.modules.video_search.index_handler import VideoSearchIndexJobHandler
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
    VideoMetadataProfileModel,
)

DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
ELASTICSEARCH_URL = os.getenv("INTEGRATION_ELASTICSEARCH_URL", "")
READY = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")) and ELASTICSEARCH_URL.startswith("http")


@unittest.skipUnless(READY, "real PostgreSQL and Elasticsearch are required")
class VideoIndexProcessingElasticsearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.marker = uuid4().hex
        self.tenant_prefix = f"video-index-es-{self.marker}"
        self.prefix = f"cam-video-proc-{self.marker[:10]}"
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            VIDEO_SEARCH_ENABLED=True,
            SEARCH_V3_ENABLED=True,
            ELASTICSEARCH_URL=ELASTICSEARCH_URL,
            ELASTICSEARCH_INDEX_PREFIX=self.prefix,
            WORKER_ID=f"video-index-es-{self.marker[:8]}",
        )
        self.physical_index = asyncio.run(self._create_index())

    def tearDown(self) -> None:
        try:
            asyncio.run(self._delete_indices())
        finally:
            with Session(self.engine) as session:
                tenant_filter = self.tenant_prefix + "%"
                session.execute(delete(ProcessingJobModel).where(
                    ProcessingJobModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(VideoAnalysisChunkModel).where(
                    VideoAnalysisChunkModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(VideoAnalysisRunModel).where(
                    VideoAnalysisRunModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(VideoMetadataProfileModel).where(
                    VideoMetadataProfileModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(SourceAssetModel).where(
                    SourceAssetModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(ExternalSourceModel).where(
                    ExternalSourceModel.tenant_id.like(tenant_filter)
                ))
                session.execute(delete(TenantProcessingPolicyModel).where(
                    TenantProcessingPolicyModel.tenant_id.like(tenant_filter)
                ))
                session.commit()
            self.engine.dispose()

    def _index(self) -> VideoSearchElasticsearchIndex:
        return VideoSearchElasticsearchIndex(
            ElasticsearchV3Config(
                ELASTICSEARCH_URL,
                index_prefix=self.prefix,
                index_generation="v3",
            )
        )

    async def _create_index(self) -> str:
        index = self._index()
        try:
            physical = await index.create_index("000001")
            await index.switch_aliases(physical)
            return physical
        finally:
            await index.aclose()

    async def _delete_indices(self) -> None:
        index = self._index()
        try:
            await index._index._request(
                "DELETE",
                f"/{self.physical_index}",
                allow_not_found=True,
            )
        finally:
            await index.aclose()

    def _create_completed_run(self, tenant_suffix: str) -> tuple[VideoAnalysisRunModel, ProcessingJobModel]:
        tenant_id = f"{self.tenant_prefix}-{tenant_suffix}"
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id=tenant_id,
                source_key=f"source-{tenant_suffix}",
                source_type="google_drive",
            )
            asset = assets.upsert_source_asset(
                tenant_id=tenant_id,
                external_source_id=source.id,
                external_asset_id=f"asset-{tenant_suffix}",
                filename="clip.mp4",
                mime_type="video/mp4",
                source_metadata={"source_type": "google_drive"},
            )
            profile = VideoMetadataProfileModel(
                tenant_id=tenant_id,
                profile_name="video",
                profile_version="v1",
                prompt_template="describe",
                active=True,
            )
            session.add_all((
                profile,
                TenantProcessingPolicyModel(
                    tenant_id=tenant_id,
                    pipeline_enabled=True,
                    search_v2_enabled=True,
                ),
            ))
            session.flush()
            run = VideoAnalysisRunModel(
                tenant_id=tenant_id,
                source_asset_id=asset.id,
                source_fingerprint=(tenant_suffix * 64)[:64],
                video_metadata_profile_id=profile.id,
                metadata_profile=profile.profile_name,
                metadata_profile_version=profile.profile_version,
                prompt_version="prompt-v1",
                analysis_version="analysis-v1",
                ai_provider="gemini",
                ai_model="model-a",
                idempotency_key=(tenant_suffix * 64)[:64],
                status="completed",
                duration_ms=3000,
                chunk_seconds=1000,
                total_chunks=2,
                completed_chunks=2,
                summary_json={"summary": "completed video"},
                completed_at=datetime.now(timezone.utc),
            )
            session.add(run)
            session.flush()
            session.add_all((
                VideoAnalysisChunkModel(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    chunk_index=0,
                    source_start_ms=0,
                    source_end_ms=1000,
                    status="completed",
                    metadata_json={"segments": [
                        {"start_ms": 600, "end_ms": 900, "summary": "second"},
                        {"start_ms": 0, "end_ms": 500, "summary": "first"},
                    ]},
                ),
                VideoAnalysisChunkModel(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    chunk_index=1,
                    source_start_ms=1000,
                    source_end_ms=2000,
                    status="completed",
                    metadata_json={"segments": [
                        {"start_ms": 1200, "end_ms": 1700, "summary": "third"},
                    ]},
                ),
            ))
            session.flush()
            self.assertTrue(enqueue_video_search_index_job(
                tenant_id=tenant_id,
                run=run,
                processing=ProcessingRepository(session),
            ))
            job = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "video_search_index",
            ))
            session.commit()
            return run, job

    def _run_worker(self, settings: Settings, job_id: str) -> ProcessingJobModel:
        runtime = build_worker_runtime(settings, session_factory=self.sessions)
        try:
            self.assertTrue(runtime.run_once())
        finally:
            runtime.close()
        with self.sessions() as session:
            return session.get(ProcessingJobModel, job_id)

    async def _stored(self, document_id: str) -> dict:
        index = self._index()
        try:
            return dict(await index.get_document(document_id))
        finally:
            await index.aclose()

    async def _count(self) -> int:
        index = self._index()
        try:
            return await index.index_count(self.physical_index)
        finally:
            await index.aclose()

    async def _mapping(self) -> dict:
        index = self._index()
        try:
            return await index.index_mapping(self.physical_index)
        finally:
            await index.aclose()

    def _invoke_handler(self, job: ProcessingJobModel) -> None:
        context = JobHandlerContext(
            job=ClaimedJob(
                job.id, job.tenant_id, job.job_type, job.entity_type, job.entity_id,
                job.payload_json, job.attempt_count, "manual-e2e",
            ),
            dependencies=WorkerDependencies(session_factory=self.sessions),
            shutdown_requested=threading.Event(),
            cancellation_requested=threading.Event(),
            logger=logging.LoggerAdapter(logging.getLogger(__name__), {}),
        )
        self.assertEqual(
            VideoSearchIndexJobHandler(self.settings)(context).outcome.value,
            "completed",
        )

    def test_real_processing_e2e_repeat_and_tenant_isolation(self) -> None:
        run_a, job_a = self._create_completed_run("a")
        stored_job = self._run_worker(self.settings, job_a.id)
        self.assertEqual(stored_job.status, "completed")
        self.assertEqual(asyncio.run(self._count()), 1)
        stored_a = asyncio.run(self._stored(
            __import__("app.modules.video_search.indexing", fromlist=["video_document_id"]).video_document_id(run_a)
        ))
        source_a = stored_a["_source"]
        self.assertEqual(source_a["tenant_id"], run_a.tenant_id)
        self.assertEqual(source_a["analysis_run_id"], run_a.id)
        self.assertEqual(source_a["source_asset_id"], run_a.source_asset_id)
        self.assertEqual(source_a["source_fingerprint"], run_a.source_fingerprint)
        self.assertEqual(
            [(segment["start_ms"], segment["end_ms"]) for segment in source_a["segments"]],
            [(0, 500), (600, 900), (1200, 1700)],
        )
        mapping = asyncio.run(self._mapping())
        self.assertEqual(mapping[self.physical_index]["mappings"]["properties"]["segments"]["type"], "nested")

        self._invoke_handler(job_a)
        repeated = asyncio.run(self._stored(
            __import__("app.modules.video_search.indexing", fromlist=["video_document_id"]).video_document_id(run_a)
        ))
        self.assertEqual(asyncio.run(self._count()), 1)
        self.assertEqual(repeated["_source"]["segments"], source_a["segments"])
        self.assertEqual(repeated["_source"]["analysis_completed_at"], source_a["analysis_completed_at"])

        run_b, job_b = self._create_completed_run("b")
        stored_b = self._run_worker(self.settings, job_b.id)
        self.assertEqual(stored_b.status, "completed")
        from app.modules.video_search.indexing import video_document_id
        self.assertNotEqual(video_document_id(run_a), video_document_id(run_b))
        self.assertEqual(asyncio.run(self._count()), 2)
        source_b = (asyncio.run(self._stored(video_document_id(run_b))))["_source"]
        self.assertEqual(source_b["tenant_id"], run_b.tenant_id)
        self.assertNotEqual(source_a["tenant_id"], source_b["tenant_id"])

    def test_offline_index_retry_preserves_completed_analysis_then_handler_indexes(self) -> None:
        run, job = self._create_completed_run("offline")
        offline_settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            VIDEO_SEARCH_ENABLED=True,
            SEARCH_V3_ENABLED=True,
            ELASTICSEARCH_URL="http://127.0.0.1:19201",
            ELASTICSEARCH_INDEX_PREFIX=self.prefix,
            WORKER_ID=f"video-index-offline-{self.marker[:8]}",
        )
        stored_job = self._run_worker(offline_settings, job.id)
        self.assertEqual(stored_job.status, "retry")
        self.assertEqual(stored_job.attempt_count, 1)
        self.assertIsNotNone(stored_job.next_attempt_at)
        self.assertIsNone(stored_job.claimed_by)
        self.assertIsNone(stored_job.lease_expires_at)
        with self.sessions() as session:
            durable_run = session.get(VideoAnalysisRunModel, run.id)
            chunks = list(session.scalars(select(VideoAnalysisChunkModel).where(
                VideoAnalysisChunkModel.run_id == run.id
            )))
            analysis_jobs = list(session.scalars(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == run.tenant_id,
                ProcessingJobModel.job_type == "video_analyze",
            )))
            self.assertEqual(durable_run.status, "completed")
            self.assertEqual(durable_run.completed_chunks, 2)
            self.assertEqual(len(chunks), 2)
            self.assertTrue(all(chunk.status == "completed" for chunk in chunks))
            self.assertEqual(analysis_jobs, [])

        self._invoke_handler(job)
        from app.modules.video_search.indexing import video_document_id
        self.assertEqual(asyncio.run(self._count()), 1)
        self.assertEqual(
            (asyncio.run(self._stored(video_document_id(run))))["_source"]["analysis_run_id"],
            run.id,
        )


if __name__ == "__main__":
    unittest.main()
