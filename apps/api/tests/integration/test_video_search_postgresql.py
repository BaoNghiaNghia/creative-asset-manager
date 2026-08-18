import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
    VideoMetadataProfileModel,
)
from app.modules.video_search.repository import VideoSearchRepository


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class VideoSearchPostgreSqlTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.marker = uuid4().hex
        self.tenant_id = f"video-pg-{self.marker}"

    def tearDown(self):
        with Session(self.engine) as session:
            session.execute(delete(VideoAnalysisChunkModel).where(
                VideoAnalysisChunkModel.tenant_id == self.tenant_id
            ))
            session.execute(delete(VideoAnalysisRunModel).where(
                VideoAnalysisRunModel.tenant_id == self.tenant_id
            ))
            session.execute(delete(VideoMetadataProfileModel).where(
                VideoMetadataProfileModel.tenant_id == self.tenant_id
            ))
            session.execute(delete(SourceAssetModel).where(SourceAssetModel.tenant_id == self.tenant_id))
            session.execute(delete(ExternalSourceModel).where(ExternalSourceModel.tenant_id == self.tenant_id))
            session.commit()
        self.engine.dispose()

    def test_repository_persists_idempotent_video_run_and_exactly_once_chunk(self):
        with Session(self.engine, expire_on_commit=False) as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id=self.tenant_id,
                source_key=f"drive-{self.marker}",
                source_type="google_drive",
            )
            source_asset = assets.upsert_source_asset(
                tenant_id=self.tenant_id,
                external_source_id=source.id,
                external_asset_id=f"clip-{self.marker}",
                mime_type="video/mp4",
            )
            profile = VideoMetadataProfileModel(
                tenant_id=self.tenant_id,
                profile_name="video",
                profile_version="v1",
                prompt_template="describe",
            )
            session.add(profile)
            session.flush()
            repository = VideoSearchRepository(session)
            values = {
                "tenant_id": self.tenant_id,
                "source_asset_id": source_asset.id,
                "source_fingerprint": "a" * 64,
                "video_metadata_profile_id": profile.id,
                "metadata_profile": profile.profile_name,
                "metadata_profile_version": profile.profile_version,
                "prompt_version": "p1",
                "analysis_version": "a1",
                "ai_provider": "gemini",
                "ai_model": "flash",
                "chunk_seconds": 30,
            }
            run = repository.get_or_create_run(**values)
            self.assertEqual(repository.get_or_create_run(**values).id, run.id)
            chunks = repository.create_chunks(
                tenant_id=self.tenant_id,
                run_id=run.id,
                layouts=[{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 1000}],
            )
            repository.mark_run_preparing(tenant_id=self.tenant_id, run_id=run.id)
            repository.mark_run_analyzing(tenant_id=self.tenant_id, run_id=run.id)
            repository.mark_chunk_preparing(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.mark_chunk_uploaded(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.mark_chunk_analyzing(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.complete_chunk(
                tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
                metadata_json={"stable": True},
            )
            repository.complete_chunk(
                tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
                metadata_json={"stable": False},
            )
            self.assertEqual(run.completed_chunks, 1)
            self.assertEqual(chunks[0].metadata_json, {"stable": True})
            self.assertEqual(repository.complete_run(
                tenant_id=self.tenant_id, run_id=run.id
            ).status, "completed")
            session.commit()
