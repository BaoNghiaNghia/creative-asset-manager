import logging
import tempfile
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, DeferredJobOutcome, JobHandlerContext, WorkerDependencies
from app.domain.providers.contracts import AiProviderError
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.modules.video_search.handler import VideoAnalyzeJobHandler
from app.modules.video_search.model import VideoAnalysisRunModel, VideoMetadataProfileModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_search.repository import VideoSearchRepository
from app.modules.video_search.proxy import PreparedVideoChunk
from app.modules.video_search.scheduler import VideoModelSelection


class VideoAnalyzeJobHandlerTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-a", source_type="google_drive")
            self.asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-a", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="checksum", provider_version="v1", source_metadata={})
            self.profile = VideoMetadataProfileModel(tenant_id="tenant-a", profile_name="video", profile_version="v1", prompt_template="describe", active=True)
            session.add(self.profile)
            session.flush()
            repo = VideoSearchRepository(session)
            run = repo.get_or_create_run(tenant_id="tenant-a", source_asset_id=self.asset.id, source_fingerprint=build_video_source_fingerprint(self.asset), video_metadata_profile_id=self.profile.id, metadata_profile="video", metadata_profile_version="v1", prompt_version="video-search-prompt-v1", analysis_version="video-search-analysis-v1", ai_provider="gemini", ai_model="model-a", chunk_seconds=30)
            repo.mark_run_preparing(tenant_id="tenant-a", run_id=run.id)
            repo.mark_run_analyzing(tenant_id="tenant-a", run_id=run.id)
            repo.complete_run(tenant_id="tenant-a", run_id=run.id)
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def _settings(self):
        return SimpleNamespace(PROCESSING_JOBS_ENABLED=True, VIDEO_SEARCH_ENABLED=True, VIDEO_ANALYSIS_ENABLED=True, VIDEO_PROXY_ENABLED=True, VIDEO_AI_REQUIRE_EXPLICIT_MODEL_LIMITS=True, GEMINI_MODEL_LIMITS="model-b:10,10", VIDEO_AI_PROMPT_VERSION="video-search-prompt-v1", VIDEO_AI_ANALYSIS_VERSION="video-search-analysis-v1", VIDEO_CHUNK_SECONDS=30, GEMINI_TIMEOUT_SECONDS=10, GEMINI_PROJECT_QUOTA_SCOPE="scope", GEMINI_MODEL_COOLDOWN_SECONDS=60, AI_EMERGENCY_STOP_ENABLED=False, GEMINI_EMERGENCY_STOP_ENABLED=False)

    def _context(self, asset_id=None):
        asset_id = asset_id or self.asset.id
        return JobHandlerContext(job=ClaimedJob(id="job-a", tenant_id="tenant-a", job_type="video_analyze", entity_type="source_asset", entity_id=asset_id, payload={"source_asset_id": asset_id}, attempt_count=1, lease_owner="worker"), dependencies=WorkerDependencies(session_factory=self.sessions), shutdown_requested=threading.Event(), cancellation_requested=threading.Event(), logger=logging.LoggerAdapter(logging.getLogger(__name__), {}))

    def test_emergency_stop_defers_before_credentials_or_provider_request(self):
        settings = self._settings()
        settings.GEMINI_EMERGENCY_STOP_ENABLED = True
        with (
            patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver,
            patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy,
        ):
            result = VideoAnalyzeJobHandler(settings)(self._context())
        self.assertIsInstance(result, DeferredJobOutcome)
        self.assertEqual(result.reason_code, "video_gemini_emergency_stop")
        resolver.assert_not_called()
        proxy.assert_not_called()

    def test_one_chunk_success_reserves_before_gemini_and_persists_result(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-b", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-b", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="checksum-b", provider_version="v1", source_metadata={})
            session.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video-proxy-test" / "00000.mp4"
            path.parent.mkdir()
            path.write_bytes(b"proxy")
            chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            analysis_result = SimpleNamespace(metadata_json={"summary": "ok"}, usage_json={"tokens": 1}, provider_metadata_json={"analysis_mode": "free_scan", "media_resolution": "MEDIA_RESOLUTION_LOW", "estimated_input_tokens": 8110})
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection
                planner.return_value.reserve.return_value = SimpleNamespace(allowed=True)
                proxy_type.return_value.prepare = AsyncMock(return_value=(chunk,))
                analysis_type.return_value.analyze_chunk = AsyncMock(return_value=analysis_result)
                result = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
            self.assertEqual(result.outcome.value, "completed")
            resolver.return_value.resolve.assert_called_once_with("tenant-a")
            self.assertEqual(planner.return_value.reserve.call_count, 1)
            self.assertEqual(analysis_type.return_value.analyze_chunk.call_count, 1)
            proxy_type.return_value.cleanup.assert_called_once()
            with self.sessions() as session:
                run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                self.assertEqual((run.status, run.total_chunks, run.completed_chunks), ("completed", 1, 1))
                stored = VideoSearchRepository(session).list_chunks(tenant_id="tenant-a", run_id=run.id)[0]
                self.assertEqual(stored.status, "completed")
                self.assertEqual(stored.metadata_json, {"summary": "ok"})
                jobs = list(session.scalars(select(ProcessingJobModel).where(
                    ProcessingJobModel.tenant_id == "tenant-a",
                    ProcessingJobModel.job_type == "video_search_index",
                )))
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0].entity_id, run.id)
                self.assertEqual(jobs[0].payload_json, {"analysis_run_id": run.id})

            # A compatible completed run is reused.  The handler must not enqueue
            # another index job and must never synchronously invoke Elasticsearch.
            with patch(
                "app.modules.video_search.index_handler.VideoSearchElasticsearchIndex"
            ) as index_type:
                repeated = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
            self.assertEqual(repeated.outcome.value, "completed")
            index_type.assert_not_called()
            with self.sessions() as fresh_session:
                fresh_run = fresh_session.scalar(select(VideoAnalysisRunModel).where(
                    VideoAnalysisRunModel.source_asset_id == asset.id
                ))
                fresh_jobs = list(fresh_session.scalars(select(ProcessingJobModel).where(
                    ProcessingJobModel.tenant_id == "tenant-a",
                    ProcessingJobModel.job_type == "video_search_index",
                )))
                self.assertEqual(fresh_run.status, "completed")
                self.assertEqual(len(fresh_jobs), 1)
                self.assertEqual(fresh_jobs[0].payload_json, {"analysis_run_id": fresh_run.id})

    def test_multi_chunk_resume_skips_completed_chunks_before_reservation(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-d", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-d", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="checksum-d", provider_version="v1", source_metadata={})
            session.commit()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "video-proxy-test"; work.mkdir()
            chunks = []
            for index in range(3):
                path = work / f"{index:05d}.mp4"; path.write_bytes(b"proxy")
                chunks.append(PreparedVideoChunk(index, path, index * 1000, (index + 1) * 1000, 1000, path.stat().st_size, 640, 360))
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            analysis_result = SimpleNamespace(metadata_json={"summary": "ok"}, usage_json={"tokens": 1}, provider_metadata_json={"analysis_mode": "free_scan", "media_resolution": "MEDIA_RESOLUTION_LOW", "estimated_input_tokens": 8110})
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection
                planner.return_value.select_pinned.return_value = selection
                planner.return_value.reserve.side_effect = [SimpleNamespace(allowed=True), SimpleNamespace(allowed=True), SimpleNamespace(allowed=False, available_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)), SimpleNamespace(allowed=True)]
                proxy_type.return_value.prepare = AsyncMock(return_value=tuple(chunks))
                analysis_type.return_value.analyze_chunk = AsyncMock(return_value=analysis_result)
                first = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
                self.assertIsInstance(first, DeferredJobOutcome)
                self.assertEqual(analysis_type.return_value.analyze_chunk.call_count, 2)
                self.assertEqual(planner.return_value.reserve.call_count, 3)
                second = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
            self.assertEqual(second.outcome.value, "completed")
            self.assertEqual(analysis_type.return_value.analyze_chunk.call_count, 3)
            self.assertEqual(planner.return_value.reserve.call_count, 4)
            with self.sessions() as session:
                run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                self.assertEqual((run.status, run.total_chunks, run.completed_chunks, run.ai_model), ("completed", 3, 3, "model-b"))

    def test_tenant_a_cannot_use_tenant_b_active_profile(self):
        with self.sessions() as session:
            session.get(VideoMetadataProfileModel, self.profile.id).active=False
            assets=AssetRegistryRepository(session); source=assets.upsert_external_source(tenant_id="tenant-a",source_key="source-no-profile",source_type="google_drive")
            asset=assets.upsert_source_asset(tenant_id="tenant-a",external_source_id=source.id,external_asset_id="asset-no-profile",filename="clip.mp4",mime_type="video/mp4",size_bytes=10,provider_checksum="no-profile",provider_version="v1",source_metadata={})
            session.add(VideoMetadataProfileModel(tenant_id="tenant-b",profile_name="video",profile_version="v1",prompt_template="describe",active=True)); session.commit()
        result=VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
        self.assertEqual(result.error_code,"video_metadata_profile_unavailable")

    def test_tenant_a_cannot_use_tenant_b_source(self):
        with self.sessions() as session:
            assets=AssetRegistryRepository(session); source=assets.upsert_external_source(tenant_id="tenant-b",source_key="source-foreign",source_type="google_drive")
            asset=assets.upsert_source_asset(tenant_id="tenant-b",external_source_id=source.id,external_asset_id="asset-foreign",filename="clip.mp4",mime_type="video/mp4",size_bytes=10,provider_checksum="foreign",provider_version="v1",source_metadata={}); session.commit()
        context=self._context(asset.id)
        with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver,patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner,patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy:
            result=VideoAnalyzeJobHandler(self._settings())(context)
        self.assertEqual(result.error_code,"video_source_unavailable"); resolver.assert_not_called(); planner.assert_not_called(); proxy.assert_not_called()

    def test_shutdown_between_chunks_preserves_completed_work(self):
        with self.sessions() as session:
            assets=AssetRegistryRepository(session); source=assets.upsert_external_source(tenant_id="tenant-a",source_key="source-between",source_type="google_drive")
            asset=assets.upsert_source_asset(tenant_id="tenant-a",external_source_id=source.id,external_asset_id="asset-between",filename="clip.mp4",mime_type="video/mp4",size_bytes=10,provider_checksum="between",provider_version="v1",source_metadata={}); session.commit()
        context=self._context(asset.id)
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)/"video-proxy-test"; work.mkdir(); chunks=[]
            for i in range(2):
                path=work/f"{i:05d}.mp4"; path.write_bytes(b"proxy"); chunks.append(PreparedVideoChunk(i,path,i*1000,(i+1)*1000,1000,path.stat().st_size,640,360))
            selection=VideoModelSelection("model-b",10000,8110,10,10,"scope:fp")
            result_data=SimpleNamespace(metadata_json={"summary":"ok"},usage_json={"tokens":1},provider_metadata_json={"analysis_mode":"free_scan"})
            async def analyze(**_kwargs):
                context.shutdown_requested.set(); return result_data
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver,patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner,patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type,patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value=SimpleNamespace(secret="key",fingerprint="fp"*32); planner.return_value.select.return_value=selection; planner.return_value.reserve.return_value=SimpleNamespace(allowed=True); proxy_type.return_value.prepare=AsyncMock(return_value=tuple(chunks)); analysis_type.return_value.analyze_chunk=AsyncMock(side_effect=analyze)
                result=VideoAnalyzeJobHandler(self._settings())(context)
            self.assertEqual(result.outcome.value,"cancelled"); self.assertEqual(planner.return_value.reserve.call_count,1); self.assertEqual(analysis_type.return_value.analyze_chunk.call_count,1); proxy_type.return_value.cleanup.assert_called_once()
            with self.sessions() as session:
                run=session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id==asset.id)); rows=VideoSearchRepository(session).list_chunks(tenant_id="tenant-a",run_id=run.id)
                self.assertEqual((run.status,run.completed_chunks,rows[0].status,rows[1].status),("cancelled",1,"completed","pending"))

    def test_shutdown_after_proxy_skips_first_chunk_and_cleans_up(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-shutdown", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-shutdown", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="shutdown", provider_version="v1", source_metadata={})
            session.commit()
        context = self._context(asset.id)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video-proxy-test" / "00000.mp4"; path.parent.mkdir(); path.write_bytes(b"proxy")
            chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            async def prepare(**_kwargs):
                context.shutdown_requested.set()
                return (chunk,)
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection; proxy_type.return_value.prepare = prepare
                result = VideoAnalyzeJobHandler(self._settings())(context)
            self.assertEqual(result.outcome.value, "cancelled")
            planner.return_value.reserve.assert_not_called(); analysis_type.assert_not_called(); proxy_type.return_value.cleanup.assert_called_once()

    def test_cancellation_during_gemini_is_cancelled_and_cleans_proxy(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-during-cancel", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-during-cancel", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="during-cancel", provider_version="v1", source_metadata={})
            session.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video-proxy-test" / "00000.mp4"; path.parent.mkdir(); path.write_bytes(b"proxy")
            chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection; planner.return_value.reserve.return_value = SimpleNamespace(allowed=True)
                proxy_type.return_value.prepare = AsyncMock(return_value=(chunk,))
                analysis_type.return_value.analyze_chunk = AsyncMock(side_effect=__import__("asyncio").CancelledError())
                result = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
            self.assertEqual(result.outcome.value, "cancelled")
            proxy_type.return_value.cleanup.assert_called_once()
            with self.sessions() as session:
                run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                stored = VideoSearchRepository(session).list_chunks(tenant_id="tenant-a", run_id=run.id)[0]
                self.assertEqual((run.status, run.completed_chunks, stored.status), ("cancelled", 0, "analyzing"))

    def test_cancellation_after_proxy_skips_reservation_and_cleans_up(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-after-cancel", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-after-cancel", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="after-cancel", provider_version="v1", source_metadata={})
            session.commit()
        context = self._context(asset.id)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video-proxy-test" / "00000.mp4"; path.parent.mkdir(); path.write_bytes(b"proxy")
            chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            async def prepare(**_kwargs):
                context.cancellation_requested.set()
                return (chunk,)
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection
                proxy_type.return_value.prepare = prepare
                result = VideoAnalyzeJobHandler(self._settings())(context)
            self.assertEqual(result.outcome.value, "cancelled")
            planner.return_value.reserve.assert_not_called()
            analysis_type.assert_not_called()
            proxy_type.return_value.cleanup.assert_called_once()
            with self.sessions() as session:
                run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                self.assertEqual(run.status, "cancelled")

    def test_cancellation_before_expensive_work_skips_provider_planning_and_proxy(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-cancel", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-cancel", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="cancel", provider_version="v1", source_metadata={})
            session.commit()
        context = self._context(asset.id)
        context.cancellation_requested.set()
        with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy:
            result = VideoAnalyzeJobHandler(self._settings())(context)
        self.assertEqual(result.outcome.value, "cancelled")
        resolver.assert_not_called(); planner.assert_not_called(); proxy.assert_not_called()

    def test_provider_error_matrix_is_classified_without_retry_or_fallback(self):
        cases = ((500, True, "gemini_video_server_error", "retryable_failure"), (None, True, "gemini_video_transport_error", "retryable_failure"), (401, False, "gemini_video_authentication_error", "non_retryable_failure"), (403, False, "gemini_video_permission_denied", "non_retryable_failure"), (None, False, "gemini_video_invalid_metadata", "non_retryable_failure"))
        for index, (status, retryable, code, outcome) in enumerate(cases):
            with self.subTest(code=code), self.sessions() as session:
                assets = AssetRegistryRepository(session)
                source = assets.upsert_external_source(tenant_id="tenant-a", source_key=f"source-error-{index}", source_type="google_drive")
                asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id=f"asset-error-{index}", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum=f"error-{index}", provider_version="v1", source_metadata={})
                session.commit()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "video-proxy-test" / "00000.mp4"; path.parent.mkdir(); path.write_bytes(b"proxy")
                chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
                selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
                with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                    resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                    planner.return_value.select.return_value = selection; planner.return_value.reserve.return_value = SimpleNamespace(allowed=True)
                    proxy_type.return_value.prepare = AsyncMock(return_value=(chunk,))
                    analysis_type.return_value.analyze_chunk = AsyncMock(side_effect=AiProviderError("failure", code=code, retryable=retryable, status_code=status))
                    result = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
                self.assertEqual(result.outcome.value, outcome)
                self.assertEqual(analysis_type.return_value.analyze_chunk.call_count, 1)
                proxy_type.return_value.cleanup.assert_called_once()
                with self.sessions() as session:
                    run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                    self.assertEqual(run.status, "failed")

    def test_rate_limit_defers_the_current_chunk_without_model_switch(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-c", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-c", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="checksum-c", provider_version="v1", source_metadata={})
            session.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video-proxy-test" / "00000.mp4"
            path.parent.mkdir(); path.write_bytes(b"proxy")
            chunk = PreparedVideoChunk(0, path, 0, 1000, 1000, path.stat().st_size, 640, 360)
            selection = VideoModelSelection("model-b", 10000, 8110, 10, 10, "scope:fp")
            with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as resolver, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_type, patch("app.modules.video_search.handler.GeminiVideoAnalysisService") as analysis_type:
                resolver.return_value.resolve.return_value = SimpleNamespace(secret="key", fingerprint="fp" * 32)
                planner.return_value.select.return_value = selection
                planner.return_value.reserve.return_value = SimpleNamespace(allowed=True)
                proxy_type.return_value.prepare = AsyncMock(return_value=(chunk,))
                analysis_type.return_value.analyze_chunk = AsyncMock(side_effect=AiProviderError("rate limited", code="gemini_video_rate_limited", retryable=True, status_code=429))
                result = VideoAnalyzeJobHandler(self._settings())(self._context(asset.id))
            self.assertIsInstance(result, DeferredJobOutcome)
            self.assertEqual(result.reason_code, "video_gemini_rate_limited")
            self.assertEqual(analysis_type.return_value.analyze_chunk.call_count, 1)
            proxy_type.return_value.cleanup.assert_called_once()
            with self.sessions() as session:
                run = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.source_asset_id == asset.id))
                stored = VideoSearchRepository(session).list_chunks(tenant_id="tenant-a", run_id=run.id)[0]
                self.assertEqual((run.ai_model, stored.status, run.completed_chunks), ("model-b", "pending", 0))

    def test_rate_limit_retry_after_accepts_only_bounded_positive_integers(self):
        settings = self._settings()
        valid = AiProviderError("limited", code="x", retryable=True, status_code=429, details={"retry_after_seconds": 7})
        fallback = AiProviderError("limited", code="x", retryable=True, status_code=429, details={"retry_after_seconds": "7"})
        valid_seconds = (VideoAnalyzeJobHandler._rate_limit_retry_at(settings, valid) - __import__("datetime").datetime.now(__import__("datetime").timezone.utc)).total_seconds()
        fallback_seconds = (VideoAnalyzeJobHandler._rate_limit_retry_at(settings, fallback) - __import__("datetime").datetime.now(__import__("datetime").timezone.utc)).total_seconds()
        self.assertGreater(valid_seconds, 5)
        self.assertGreater(fallback_seconds, settings.GEMINI_MODEL_COOLDOWN_SECONDS - 2)

    def test_completed_compatible_run_skips_planner_quota_proxy_and_gemini(self):
        with patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as credentials, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy, patch("app.modules.video_search.handler.GeminiVideoClient") as client:
            result = VideoAnalyzeJobHandler(self._settings())(self._context())
        self.assertEqual(result.outcome.value, "completed")
        planner.assert_not_called()
        credentials.assert_not_called()
        proxy.assert_not_called()
        client.assert_not_called()

    def test_completed_reuse_does_not_require_current_gemini_limits(self):
        settings = self._settings()
        settings.GEMINI_MODEL_LIMITS = ""
        with patch("app.modules.video_search.handler.CreativeGeminiCredentialResolver") as credentials, patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy:
            result = VideoAnalyzeJobHandler(settings)(self._context())
        self.assertEqual(result.outcome.value, "completed")
        credentials.assert_not_called()
        planner.assert_not_called()
        proxy.assert_not_called()

    def test_explicit_model_limits_are_required_before_proxy_or_quota(self):
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(tenant_id="tenant-a", source_key="source-limits", source_type="google_drive")
            asset = assets.upsert_source_asset(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="asset-limits", filename="clip.mp4", mime_type="video/mp4", size_bytes=10, provider_checksum="limits", provider_version="v1", source_metadata={})
            session.commit()
        settings = self._settings()
        settings.GEMINI_MODEL_LIMITS = ""
        with patch("app.modules.video_search.handler.VideoFreeTierModelPlanner") as planner, patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy:
            result = VideoAnalyzeJobHandler(settings)(self._context(asset.id))
        self.assertEqual(result.outcome.value, "non_retryable_failure")
        self.assertEqual(result.error_code, "video_gemini_limits_not_explicitly_configured")
        planner.assert_not_called()
        proxy.assert_not_called()
