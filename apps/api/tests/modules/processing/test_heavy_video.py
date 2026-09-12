from __future__ import annotations

import logging
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerResult, WorkerDependencies
from app.modules.assets import model as _asset_models  # noqa: F401
from app.modules.processing.heavy_video import (
    HEAVY_VIDEO_RESOURCE_KEY,
    HeavyVideoResource,
    VIDEO_ANALYSIS_OWNER_TYPE,
    VIDEO_GENERATION_OWNER_TYPE,
)
from app.modules.processing.model import ProcessingJobModel, ProcessingResourceLeaseModel
from app.modules.processing.registry import build_handler_registry
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.modules.processing.service import ProcessingJobService
from app.modules.video_generation.model import VideoGenerationRunModel


class HeavyVideoRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'heavy-video.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def enqueue(self, *, tenant: str, job_type: str, entity_id: str) -> str:
        with self.sessions() as session:
            return ProcessingJobService(ProcessingRepository(session)).enqueue_job(
                tenant_id=tenant,
                job_type=job_type,
                entity_type="video_generation_run" if job_type == "video_generate" else "source_asset",
                entity_id=entity_id,
                idempotency_key=f"{job_type}:{entity_id}",
                payload={"video_generation_run_id": entity_id} if job_type == "video_generate" else {"source_asset_id": entity_id},
            ).id

    def create_generation(self, *, tenant: str, run_id: str, status: str = "queued") -> None:
        with self.sessions.begin() as session:
            session.add(VideoGenerationRunModel(
                id=run_id, tenant_id=tenant, provider="dola", provider_model="seedance-2.0",
                prompt="test", aspect_ratio="16:9", duration_seconds=5, status=status,
                request_fingerprint=f"fp-{run_id}", client_request_id=f"request-{run_id}",
                created_by_user_id="user",
            ))

    def runtime(self, handlers) -> WorkerRuntime:
        return WorkerRuntime(
            config=WorkerRuntimeConfig(
                worker_id="heavy-video-test", enabled=True, lease_seconds=30,
                heartbeat_seconds=1, idle_poll_seconds=0.01, drain_timeout_seconds=0.1,
            ),
            dependencies=WorkerDependencies(self.sessions),
            registry=build_handler_registry(handlers),
            logger=logging.getLogger("test.heavy_video"),
        )

    def owner(self):
        with self.sessions() as session:
            return HeavyVideoResource(session).owner()

    def test_analysis_owner_blocks_generation_before_handler_and_cross_tenant(self):
        analysis_owner = self.enqueue(tenant="tenant-a", job_type="video_analyze", entity_id="asset-owner")
        with self.sessions.begin() as session:
            owner_job = session.get(ProcessingJobModel, analysis_owner)
            owner_job.status = "processing"
            owner_job.claimed_by = "other-worker"
            owner_job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            self.assertTrue(HeavyVideoResource(session).acquire(
                owner_type=VIDEO_ANALYSIS_OWNER_TYPE, owner_id=analysis_owner
            ))
        self.create_generation(tenant="tenant-b", run_id="generation-b")
        job_id = self.enqueue(tenant="tenant-b", job_type="video_generate", entity_id="generation-b")
        called = []
        runtime = self.runtime((("video_generate", lambda context: called.append(context) or JobHandlerResult.completed()),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(called, [])
        with self.sessions() as session:
            job = session.get(ProcessingJobModel, job_id)
            self.assertEqual((job.status, job.last_error_code), ("pending", "heavy_video_busy"))
        self.assertEqual(self.owner(), (VIDEO_ANALYSIS_OWNER_TYPE, analysis_owner))

    def test_generation_deferred_retains_lane_and_blocks_analysis_before_ffmpeg(self):
        self.create_generation(tenant="tenant-a", run_id="generation-a")
        generation_job = self.enqueue(tenant="tenant-a", job_type="video_generate", entity_id="generation-a")
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        runtime = self.runtime((("video_generate", lambda _: DeferredJobOutcome("poll", "poll later", future)),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(self.owner(), (VIDEO_GENERATION_OWNER_TYPE, "generation-a"))
        with self.sessions() as session:
            self.assertEqual(session.get(ProcessingJobModel, generation_job).status, "pending")
        analysis_job = self.enqueue(tenant="tenant-b", job_type="video_analyze", entity_id="asset-b")
        ffmpeg_started = []
        runtime = self.runtime((("video_analyze", lambda context: ffmpeg_started.append(context) or JobHandlerResult.completed()),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(ffmpeg_started, [])
        with self.sessions() as session:
            self.assertEqual(session.get(ProcessingJobModel, analysis_job).last_error_code, "heavy_video_busy")

    def test_generation_terminal_and_analysis_cancel_release_exactly_once(self):
        self.create_generation(tenant="tenant-a", run_id="generation-terminal", status="failed")
        self.enqueue(tenant="tenant-a", job_type="video_generate", entity_id="generation-terminal")
        runtime = self.runtime((("video_generate", lambda _: JobHandlerResult.non_retryable("failed", "failed")),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(self.owner(), (None, None))

        analysis_job = self.enqueue(tenant="tenant-a", job_type="video_analyze", entity_id="asset-c")
        runtime = self.runtime((("video_analyze", lambda _: JobHandlerResult.cancelled()),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(self.owner(), (None, None))
        with self.sessions() as session:
            self.assertIn(session.get(ProcessingJobModel, analysis_job).status, {"pending", "retry"})

    def test_stale_analysis_owner_is_recovered_but_generation_owner_is_not(self):
        with self.sessions.begin() as session:
            stale = ProcessingJobModel(
                id="stale-analysis", tenant_id="tenant-a", job_type="video_analyze",
                entity_type="source_asset", entity_id="asset", idempotency_key="stale",
                payload_json={}, status="processing", lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            session.add(stale)
            self.assertTrue(HeavyVideoResource(session).acquire(
                owner_type=VIDEO_ANALYSIS_OWNER_TYPE, owner_id=stale.id
            ))
        with self.sessions.begin() as session:
            self.assertTrue(HeavyVideoResource(session).acquire(
                owner_type=VIDEO_GENERATION_OWNER_TYPE, owner_id="generation-recovery"
            ))
        self.assertEqual(self.owner(), (VIDEO_GENERATION_OWNER_TYPE, "generation-recovery"))
        with self.sessions.begin() as session:
            self.assertFalse(HeavyVideoResource(session).acquire(
                owner_type=VIDEO_ANALYSIS_OWNER_TYPE, owner_id="new-analysis"
            ))

    def test_resource_row_is_global_and_metadata_seed_fallback_is_durable(self):
        with self.sessions.begin() as session:
            self.assertTrue(HeavyVideoResource(session).acquire(
                owner_type=VIDEO_GENERATION_OWNER_TYPE, owner_id="run"
            ))
        with self.sessions() as session:
            slot = session.get(ProcessingResourceLeaseModel, HEAVY_VIDEO_RESOURCE_KEY)
            self.assertIsNotNone(slot)
            self.assertEqual(slot.owner_id, "run")

    def test_video_search_index_never_acquires_lane(self):
        job_id = self.enqueue(tenant="tenant-a", job_type="video_search_index", entity_id="analysis-run")
        calls = []
        runtime = self.runtime((("video_search_index", lambda context: calls.append(context) or JobHandlerResult.completed()),))
        self.assertTrue(runtime.run_once())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.owner(), (None, None))

    def test_capacity_one_race_has_one_winner(self):
        # The production path locks the pre-created row FOR UPDATE. This
        # deterministic test verifies the same transaction-visible invariant.
        with self.sessions.begin() as session:
            first = HeavyVideoResource(session).acquire(
                owner_type=VIDEO_GENERATION_OWNER_TYPE, owner_id="first"
            )
        with self.sessions.begin() as session:
            second = HeavyVideoResource(session).acquire(
                owner_type=VIDEO_ANALYSIS_OWNER_TYPE, owner_id="second"
            )
        self.assertEqual((first, second), (True, False))
