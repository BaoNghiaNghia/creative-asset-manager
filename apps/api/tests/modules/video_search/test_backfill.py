import unittest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.backfill import VideoAnalysisBackfillService
from app.modules.video_search.model import VideoMetadataProfileModel
from app.modules.video_search.repository import VideoSearchRepository


class VideoBackfillTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.settings = Settings(PROCESSING_JOBS_ENABLED=True, VIDEO_SEARCH_ENABLED=True, VIDEO_ANALYSIS_ENABLED=True, VIDEO_PROXY_ENABLED=True)
        self.assets = AssetRegistryRepository(self.session)
        self.service = VideoAnalysisBackfillService(ProcessingRepository(self.session), settings=self.settings)

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def asset(self, tenant="tenant-a", external_id="video", *, mime="video/mp4", checksum="one"):
        source = self.assets.upsert_external_source(tenant_id=tenant, source_key=f"source-{tenant}", source_type="google_drive")
        return self.assets.upsert_source_asset(tenant_id=tenant, external_source_id=source.id, external_asset_id=external_id, filename=external_id, mime_type=mime, provider_checksum=checksum, provider_version=checksum)

    def profile(self, tenant="tenant-a", version="v1"):
        value = VideoMetadataProfileModel(tenant_id=tenant, profile_name="video", profile_version=version, prompt_template="describe", active=True)
        self.session.add(value); self.session.flush()
        return value

    def execute_backfill(self, **kwargs):
        return self.service.run(**kwargs)

    def test_first_repeat_filter_and_dry_run(self):
        asset = self.asset(); self.profile()
        first = self.execute_backfill(tenant_id="tenant-a")
        self.assertEqual((first.scanned, first.eligible, first.enqueued), (1, 1, 1))
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a").enqueued, 0)
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a", source_asset_id="missing").scanned, 0)
        other = self.asset(external_id="video-two")
        dry = self.execute_backfill(tenant_id="tenant-a", source_asset_id=other.id, dry_run=True)
        self.assertEqual((dry.enqueued, self.session.query(ProcessingJobModel).count()), (1, 1))

    def test_completed_and_resumable_runs_skip(self):
        asset = self.asset(); profile = self.profile()
        repo = VideoSearchRepository(self.session)
        values = dict(tenant_id="tenant-a", source_asset_id=asset.id, source_fingerprint=__import__("app.modules.video_search.fingerprint", fromlist=["build_video_source_fingerprint"]).build_video_source_fingerprint(asset), video_metadata_profile_id=profile.id, metadata_profile=profile.profile_name, metadata_profile_version=profile.profile_version, prompt_version=self.settings.VIDEO_AI_PROMPT_VERSION, analysis_version=self.settings.VIDEO_AI_ANALYSIS_VERSION, ai_provider="gemini", ai_model="model-a", chunk_seconds=30)
        resumable = repo.get_or_create_run(**values)
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a").skipped_existing_job, 1)
        repo.mark_run_preparing(tenant_id="tenant-a", run_id=resumable.id); repo.mark_run_analyzing(tenant_id="tenant-a", run_id=resumable.id); repo.complete_run(tenant_id="tenant-a", run_id=resumable.id)
        result = self.execute_backfill(tenant_id="tenant-a")
        self.assertEqual(result.skipped_completed, 1)

    def test_tenant_profile_change_unsupported_and_no_profile(self):
        a = self.asset(); b = self.asset("tenant-b", "b")
        self.profile("tenant-a", "v1")
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a").enqueued, 1)
        self.assertEqual(self.execute_backfill(tenant_id="tenant-b").skipped_no_profile, 1)
        self.profile("tenant-b", "v1")
        self.assertEqual(self.execute_backfill(tenant_id="tenant-b").enqueued, 1)
        unsupported = self.asset(external_id="pdf", mime="application/pdf")
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a", source_asset_id=unsupported.id).skipped_unsupported, 1)
        self.profile("tenant-a", "v2")
        self.assertEqual(self.execute_backfill(tenant_id="tenant-a", source_asset_id=a.id).enqueued, 1)

    def test_limit_is_stable_and_payload_has_no_model_or_provider_uri(self):
        self.asset(external_id="a"); self.asset(external_id="b"); self.profile()
        result = self.execute_backfill(tenant_id="tenant-a", limit=1)
        self.assertEqual((result.scanned, result.enqueued), (1, 1))
        job = self.session.scalar(select(ProcessingJobModel))
        self.assertNotIn("ai_model", job.payload_json)
        self.assertFalse(any("uri" in key.lower() or "secret" in key.lower() for key in job.payload_json))


if __name__ == "__main__":
    unittest.main()
