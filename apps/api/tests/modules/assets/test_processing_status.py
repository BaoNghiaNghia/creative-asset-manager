import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.assets.status_service import AssetProcessingStatusService
from app.modules.metadata.router import router
from app.modules.processing.model import ProcessingJobModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.search.operations_model import (
    SearchOperationItemModel,
    SearchOperationRunModel,
)
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.video_search.model import VideoAnalysisRunModel, VideoMetadataProfileModel

TENANT = "google-drive:developer"


class AssetProcessingStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.assets = AssetRegistryRepository(self.session)
        self.source = self.assets.upsert_external_source(
            tenant_id=TENANT,
            source_key="drive-primary",
            source_type="google_drive",
        )
        self.profile = MetadataProfileModel(
            tenant_id=TENANT,
            profile_name="default",
            profile_version="1",
            prompt_template="describe",
        )
        self.session.add(self.profile)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def add_item(self, item_id: str, hash_character: str):
        source_asset = self.assets.upsert_source_asset(
            tenant_id=TENANT,
            external_source_id=self.source.id,
            external_asset_id=item_id,
            filename=item_id + ".jpg",
            mime_type="image/jpeg",
        )
        asset = self.assets.create_asset(
            tenant_id=TENANT,
            content_hash=hash_character * 64,
            mime_type="image/jpeg",
        )
        self.assets.link_source_asset(
            tenant_id=TENANT,
            asset_id=asset.id,
            source_asset_id=source_asset.id,
        )
        return source_asset, asset

    def add_analysis(self, asset, status: str, metadata=None):
        analysis = AssetAiAnalysisModel(
            tenant_id=TENANT,
            asset_id=asset.id,
            content_hash=asset.content_hash,
            metadata_profile_id=self.profile.id,
            metadata_profile="default",
            metadata_profile_version="1",
            prompt_version="1",
            pipeline_version="1",
            status=status,
            metadata_json=metadata,
        )
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def test_status_projection_covers_lifecycle_and_precedence(self) -> None:
        self.assets.upsert_source_asset(
            tenant_id=TENANT,
            external_source_id=self.source.id,
            external_asset_id="discovered",
        )

        _, stored = self.add_item("stored", "a")
        self.session.add(
            AssetStorageObjectModel(
                tenant_id=TENANT,
                asset_id=stored.id,
                content_hash=stored.content_hash,
                storage_provider="google_drive",
                status="stored",
            )
        )

        _, duplicate = self.add_item("duplicate", "b")
        duplicate_source = self.assets.upsert_source_asset(
            tenant_id=TENANT,
            external_source_id=self.source.id,
            external_asset_id="duplicate-origin",
        )
        self.assets.link_source_asset(
            tenant_id=TENANT,
            asset_id=duplicate.id,
            source_asset_id=duplicate_source.id,
        )

        _, analyzing = self.add_item("analyzing", "c")
        self.add_analysis(analyzing, "running")

        _, metadata_ready = self.add_item("metadata-ready", "d")
        self.add_analysis(metadata_ready, "completed", {"subject": "cat"})

        _, indexed = self.add_item("indexed", "e")
        indexed_analysis = self.add_analysis(
            indexed,
            "completed",
            {"subject": "dog"},
        )
        indexed_analysis.search_projection = {"search_text": "dog"}
        indexed_analysis.search_projection_version = "v2"
        run = SearchOperationRunModel(
            tenant_id=TENANT,
            operation_type="reindex_assets",
            status="completed",
            filters_json={},
            target_projection_version="v2",
            target_index="assets-v2-1",
            page_size=100,
            dry_run=False,
            cancellation_requested=False,
        )
        self.session.add(run)
        self.session.flush()
        self.session.add(
            SearchOperationItemModel(
                run_id=run.id,
                tenant_id=TENANT,
                analysis_id=indexed_analysis.id,
                asset_id=indexed.id,
                status="completed",
            )
        )

        failed_source, _ = self.add_item("failed", "f")
        self.session.add(
            ProcessingJobModel(
                tenant_id=TENANT,
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=failed_source.id,
                idempotency_key="failed-source-download",
                payload_json={},
                status="failed",
            )
        )
        self.session.commit()

        item_ids = [
            "discovered",
            "stored",
            "duplicate",
            "analyzing",
            "metadata-ready",
            "indexed",
            "failed",
        ]
        statuses = AssetProcessingStatusService(self.session).list(
            TENANT,
            "google-drive",
            item_ids,
        )

        self.assertEqual(
            statuses,
            {
                "discovered": "discovered",
                "stored": "stored",
                "duplicate": "duplicate",
                "analyzing": "analyzing",
                "metadata-ready": "search_pending",
                "indexed": "indexed",
                "failed": "failed",
            },
        )

    def test_video_analysis_and_index_jobs_drive_provider_item_status(self) -> None:
        profile = VideoMetadataProfileModel(
            tenant_id=TENANT,
            profile_name="video-default",
            profile_version="1",
            prompt_template="analyze video",
            search_config_json={},
        )
        self.session.add(profile)
        self.session.flush()

        def add_video(item_id: str, fingerprint: str, run_status: str):
            source_asset = self.assets.upsert_source_asset(
                tenant_id=TENANT,
                external_source_id=self.source.id,
                external_asset_id=item_id,
                filename=item_id + ".mp4",
                mime_type="video/mp4",
            )
            run = VideoAnalysisRunModel(
                tenant_id=TENANT,
                source_asset_id=source_asset.id,
                source_fingerprint=fingerprint * 64,
                video_metadata_profile_id=profile.id,
                metadata_profile="video-default",
                metadata_profile_version="1",
                prompt_version="1",
                analysis_version="1",
                ai_provider="gemini",
                ai_model="gemini-test",
                idempotency_key=fingerprint * 64,
                status=run_status,
                chunk_seconds=30,
                total_chunks=1,
                completed_chunks=1 if run_status == "completed" else 0,
                summary_json={"summary": "complete"} if run_status == "completed" else None,
            )
            self.session.add(run)
            self.session.flush()
            return source_asset, run

        add_video("video-analyzing", "1", "analyzing")
        _, completed_run = add_video("video-completed", "2", "completed")
        _, indexed_run = add_video("video-indexed", "3", "completed")
        self.session.add_all([
            ProcessingJobModel(
                tenant_id=TENANT,
                job_type="video_search_index",
                entity_type="video_analysis_run",
                entity_id=completed_run.id,
                idempotency_key="video-index-pending",
                payload_json={"analysis_run_id": completed_run.id},
                status="pending",
            ),
            ProcessingJobModel(
                tenant_id=TENANT,
                job_type="video_search_index",
                entity_type="video_analysis_run",
                entity_id=indexed_run.id,
                idempotency_key="video-index-completed",
                payload_json={"analysis_run_id": indexed_run.id},
                status="completed",
            ),
        ])
        self.session.commit()

        self.assertEqual(
            AssetProcessingStatusService(self.session).list(
                TENANT,
                "google-drive",
                ["video-analyzing", "video-completed", "video-indexed"],
            ),
            {
                "video-analyzing": "analyzing",
                "video-completed": "search_pending",
                "video-indexed": "indexed",
            },
        )

    def test_provider_and_tenant_scope_are_enforced(self) -> None:
        self.add_item("private-item", "9")
        self.session.commit()

        service = AssetProcessingStatusService(self.session)
        self.assertEqual(
            service.list("another-tenant", "google-drive", ["private-item"]),
            {"private-item": "discovered"},
        )
        self.assertEqual(
            service.list(TENANT, "sharepoint", ["private-item"]),
            {"private-item": "discovered"},
        )


    def test_completed_pipeline_index_job_marks_provider_item_indexed(self) -> None:
        source_asset, asset = self.add_item("pipeline-indexed", "7")
        pipeline = AssetPipelineModel(
            tenant_id=TENANT,
            correlation_id="pipeline-indexed",
            origin_type="source_asset",
            origin_id=source_asset.id,
            source_asset_id=source_asset.id,
            asset_id=asset.id,
            state="search_pending",
        )
        self.session.add(pipeline)
        self.session.flush()
        self.session.add(
            ProcessingJobModel(
                tenant_id=TENANT,
                job_type="asset_index",
                entity_type="asset_pipeline",
                entity_id=pipeline.id,
                idempotency_key="pipeline-indexed-job",
                payload_json={"pipeline_id": pipeline.id},
                status="completed",
            )
        )
        self.session.commit()

        self.assertEqual(
            AssetProcessingStatusService(self.session).list(
                TENANT,
                "google-drive",
                ["pipeline-indexed"],
            ),
            {"pipeline-indexed": "indexed"},
        )


class AssetProcessingStatusApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        registry = AssetRegistryRepository(self.session)
        source = registry.upsert_external_source(
            tenant_id=TENANT,
            source_key="drive-primary",
            source_type="google_drive",
        )
        source_asset = registry.upsert_source_asset(
            tenant_id=TENANT,
            external_source_id=source.id,
            external_asset_id="stored-item",
        )
        asset = registry.create_asset(
            tenant_id=TENANT,
            content_hash="7" * 64,
        )
        registry.link_source_asset(
            tenant_id=TENANT,
            asset_id=asset.id,
            source_asset_id=source_asset.id,
        )
        self.session.add(
            AssetStorageObjectModel(
                tenant_id=TENANT,
                asset_id=asset.id,
                content_hash=asset.content_hash,
                storage_provider="google_drive",
                status="stored",
            )
        )
        self.session.commit()

        app = FastAPI()
        app.include_router(router)

        def database_override():
            yield self.session

        app.dependency_overrides[get_db] = database_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.session.close()
        self.engine.dispose()

    def test_query_and_rating_responses_include_current_status(self) -> None:
        query = self.client.post(
            "/metadata/query",
            json={
                "provider": "google-drive",
                "item_ids": ["stored-item", "unknown-item"],
            },
        )
        rating = self.client.put(
            "/metadata/rating",
            json={
                "provider": "google-drive",
                "item_ids": ["stored-item"],
                "rating": 5,
            },
        )

        self.assertEqual(query.status_code, 200)
        self.assertEqual(
            [item["processing_status"] for item in query.json()["items"]],
            ["stored", "discovered"],
        )
        self.assertEqual(rating.status_code, 200)
        self.assertEqual(rating.json()["items"][0]["processing_status"], "stored")


if __name__ == "__main__":
    unittest.main()
