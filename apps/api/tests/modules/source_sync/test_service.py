import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.providers.contracts import (
    ExternalAssetCandidate,
    SourceChange,
    SourceChangePage,
)
from app.modules.assets.model import SourceAssetModel, SourceSyncCursorModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.repository import SourceSyncRepository
from app.modules.source_sync.service import SourceSyncService


def candidate(
    external_id: str,
    *,
    name: str = "asset.png",
    mime_type: str = "image/png",
    is_folder: bool = False,
    checksum: str | None = "hash-v1",
    parent: str = "folder-a",
) -> ExternalAssetCandidate:
    return ExternalAssetCandidate(
        source_type="google_drive",
        source_id="source-db-id",
        external_asset_id=external_id,
        filename=name,
        mime_type=mime_type,
        source_modified_at="2026-07-18T08:00:00Z",
        provider_checksum=checksum,
        source_metadata={"parents": [parent], "is_folder": is_folder},
    )


class FakeProvider:
    def __init__(self, pages):
        self.pages = list(pages)
        self.inputs = []

    async def list_changes(self, input):
        self.inputs.append(input)
        return self.pages.pop(0)

    async def get_asset(self, input):
        raise NotImplementedError

    async def open_download_stream(self, input):
        raise NotImplementedError


class SourceSyncServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        assets = AssetRegistryRepository(self.session)
        self.source = assets.upsert_external_source(
            tenant_id="tenant-a",
            source_key="drive-primary",
            source_type="google_drive",
            source_metadata={"drive_id": "drive-1"},
        )
        self.session.commit()
        self.assets = assets
        self.repository = SourceSyncRepository(self.session)
        self.processing = ProcessingRepository(self.session)
        self.service = SourceSyncService(
            self.repository, self.processing, enabled=True
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    async def test_internal_managed_candidate_is_retired_without_ingestion_or_job(self) -> None:
        managed = ExternalAssetCandidate(
            source_type="google_drive",
            source_id="source-db-id",
            external_asset_id="managed-copy",
            filename="staging.png",
            mime_type="image/png",
            source_metadata={
                "parents": ["managed-root"],
                "app_properties": {
                    "cam_tenant_id": "tenant-a",
                    "cam_asset_id": "asset-a",
                    "cam_content_hash": "a" * 64,
                },
            },
        )
        existing = self.assets.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="managed-copy",
            filename="old-staging.png",
            source_metadata={"parents": ["managed-root"]},
        )
        self.session.commit()
        provider = FakeProvider([
            SourceChangePage((SourceChange("updated", "managed-copy", managed),), "cursor", False)
        ])

        result = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id, provider=provider
        )

        self.assertEqual(result.jobs_created, 0)
        self.assertTrue(self.session.get(SourceAssetModel, existing.id).deleted_at is not None)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ProcessingJobModel)),
            0,
        )

    async def test_deleted_linked_source_enqueues_idempotent_search_sync(self) -> None:
        self.service = SourceSyncService(
            self.repository,
            self.processing,
            enabled=True,
            settings=Settings(
                PROCESSING_JOBS_ENABLED=True,
                UNIFIED_ASSET_INGESTION_ENABLED=True,
                SEARCH_V3_ENABLED=True,
            ),
        )
        source_asset = self.assets.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="file-deleted",
            filename="deleted.png",
            mime_type="image/png",
        )
        asset = self.assets.create_asset(
            tenant_id="tenant-a",
            content_hash="a" * 64,
            mime_type="image/png",
        )
        self.assets.link_source_asset(
            tenant_id="tenant-a",
            asset_id=asset.id,
            source_asset_id=source_asset.id,
        )
        self.session.commit()

        provider = FakeProvider([
            SourceChangePage(
                (SourceChange("deleted", "file-deleted", None),),
                "cursor",
                False,
            )
        ])
        await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id, provider=provider
        )

        job = self.session.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type == "search_index_sync"
        ))
        self.assertIsNotNone(job)
        self.assertEqual(job.entity_id, asset.id)
        self.assertEqual(job.payload_json["asset_id"], asset.id)
        self.assertTrue(
            self.session.get(SourceAssetModel, source_asset.id).deleted_at
        )

    async def test_persists_each_page_before_advancing_cursor(self) -> None:
        provider = FakeProvider(
            [
                SourceChangePage(
                    (SourceChange("updated", "file-1", candidate("file-1")),),
                    "page-2",
                    True,
                ),
                SourceChangePage(
                    (SourceChange("updated", "file-2", candidate("file-2")),),
                    "stable-cursor",
                    False,
                ),
            ]
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id, provider=provider
        )
        self.assertEqual(result.pages, 2)
        self.assertEqual(provider.inputs[0].cursor, None)
        self.assertEqual(provider.inputs[1].cursor, "page-2")
        self.assertEqual(
            self.repository.get_cursor("tenant-a", self.source.id), "stable-cursor"
        )
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(SourceAssetModel)), 2
        )
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2
        )

    async def test_failed_page_does_not_advance_cursor_or_persist_assets(self) -> None:
        self.assets.save_sync_cursor(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            cursor_value="cursor-before",
        )
        self.session.commit()
        provider = FakeProvider(
            [
                SourceChangePage(
                    (
                        SourceChange("updated", "file-1", candidate("file-1")),
                        SourceChange("updated", "broken", None),
                    ),
                    "cursor-after",
                    False,
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "requires a candidate"):
            await self.service.sync_source(
                tenant_id="tenant-a", source_id=self.source.id, provider=provider
            )
        self.assertEqual(
            self.repository.get_cursor("tenant-a", self.source.id), "cursor-before"
        )
        self.assertIsNone(
            self.repository.get_source_asset_by_external_id(
                "tenant-a", self.source.id, "file-1"
            )
        )

    def _enable_search_projection_sync(self):
        self.service = SourceSyncService(
            self.repository,
            self.processing,
            enabled=True,
            settings=Settings(
                PROCESSING_JOBS_ENABLED=True,
                UNIFIED_ASSET_INGESTION_ENABLED=True,
                SEARCH_V3_ENABLED=True,
            ),
        )
        source_asset = self.assets.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="linked-file",
            filename="before.pdf",
            mime_type="application/pdf",
            size_bytes=12,
            provider_checksum="same-content",
            source_modified_at=datetime(2026, 7, 18, 8, tzinfo=timezone.utc),
            source_metadata={"parents": ["folder-b"], "path": "Outside"},
        )
        asset = self.assets.create_asset(
            tenant_id="tenant-a",
            content_hash="b" * 64,
            mime_type="application/pdf",
        )
        self.assets.link_source_asset(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id
        )
        self.session.commit()
        return source_asset, asset

    async def test_move_and_rename_enqueue_search_sync_without_download(self) -> None:
        source_asset, asset = self._enable_search_projection_sync()
        moved = ExternalAssetCandidate(
            source_type="google_drive",
            source_id="source-db-id",
            external_asset_id="linked-file",
            filename="inside.txt",
            mime_type="application/pdf",
            provider_checksum="same-content",
            source_modified_at="2026-07-18T08:00:00Z",
            source_metadata={"parents": ["folder-a"], "path": "Allowed"},
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "linked-file", moved),), "done")]),
        )

        jobs = list(self.session.scalars(select(ProcessingJobModel)))
        self.assertEqual(result.jobs_created, 0)
        self.assertIn("search_index_sync", [job.job_type for job in jobs])
        self.assertEqual(jobs[0].entity_id, asset.id)
        self.assertEqual(jobs[0].payload_json["source_asset_id"], source_asset.id)
        changed = self.session.get(SourceAssetModel, source_asset.id)
        self.assertEqual(changed.filename, "inside.txt")
        self.assertEqual(changed.source_metadata["parents"], ["folder-a"])
        self.assertEqual(changed.source_metadata["path"], "Allowed")

    async def test_projection_noop_does_not_enqueue_duplicate_search_sync(self) -> None:
        self._enable_search_projection_sync()
        unchanged = ExternalAssetCandidate(
            source_type="google_drive",
            source_id="source-db-id",
            external_asset_id="linked-file",
            filename="before.pdf",
            mime_type="application/pdf",
            size_bytes=12,
            provider_checksum="same-content",
            source_modified_at="2026-07-18T08:00:00Z",
            source_metadata={"parents": ["folder-b"], "path": "Outside"},
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "linked-file", unchanged),), "done")]),
        )
        self.assertEqual(result.jobs_created, 0)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "search_index_sync"
            )),
            0,
        )

    async def test_rename_and_move_do_not_create_another_download_job(self) -> None:
        first = FakeProvider(
            [SourceChangePage((SourceChange("updated", "file-1", candidate("file-1")),), "c1")]
        )
        await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id, provider=first
        )
        renamed = FakeProvider(
            [
                SourceChangePage(
                    (
                        SourceChange(
                            "updated",
                            "file-1",
                            candidate("file-1", name="renamed.png", parent="folder-b"),
                        ),
                    ),
                    "c2",
                )
            ]
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id, provider=renamed
        )
        asset = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "file-1"
        )
        self.assertEqual(asset.filename, "renamed.png")
        self.assertEqual(asset.source_metadata["parents"], ["folder-b"])
        self.assertEqual(result.jobs_created, 0)
        self.assertEqual(self.processing.count_jobs(), 1)

    async def test_unchanged_supported_legacy_asset_without_pipeline_is_enqueued_once(self) -> None:
        legacy = self.assets.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="legacy-avif",
            filename="legacy.avif",
            mime_type="image/avif",
            provider_checksum="hash-v1",
        )
        self.session.commit()
        unchanged = candidate(
            "legacy-avif", name="legacy.avif", mime_type="image/avif", checksum="hash-v1"
        )

        first = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "legacy-avif", unchanged),), "c1")]),
        )
        repeated = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "legacy-avif", unchanged),), "c2")]),
        )

        jobs = list(self.session.scalars(select(ProcessingJobModel)))
        self.assertEqual((first.jobs_created, repeated.jobs_created), (1, 0))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0].idempotency_key,
            f"source-asset-download:{legacy.id}:initial-import-v2",
        )

    async def test_changed_legacy_asset_without_pipeline_keeps_one_stable_initial_job(self) -> None:
        self.assets.upsert_source_asset(
            tenant_id="tenant-a", external_source_id=self.source.id,
            external_asset_id="legacy-heic", filename="legacy.heic",
            mime_type="image/heic", provider_checksum="old-hash",
        )
        self.session.commit()
        changed = candidate(
            "legacy-heic", name="legacy.heic", mime_type="image/heic", checksum="new-hash"
        )

        first = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "legacy-heic", changed),), "c1")]),
        )
        repeated = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=FakeProvider([SourceChangePage((SourceChange("updated", "legacy-heic", changed),), "c2")]),
        )

        self.assertEqual((first.jobs_created, repeated.jobs_created), (1, 0))
        self.assertEqual(self.processing.count_jobs(), 1)

    async def test_source_change_invalidates_viewer_hierarchy_cache(self) -> None:
        with patch(
            "app.modules.source_sync.service._invalidate_viewer_folder_hierarchy"
        ) as invalidate:
            await self.service.sync_source(
                tenant_id="tenant-a",
                source_id=self.source.id,
                provider=FakeProvider(
                    [
                        SourceChangePage(
                            (SourceChange("updated", "file-1", candidate("file-1")),),
                            "c1",
                        )
                    ]
                ),
            )

        invalidate.assert_called_once_with(
            tenant_id="tenant-a", external_source_id=self.source.id
        )

    async def test_overwrite_creates_new_version_job(self) -> None:
        await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider(
                [SourceChangePage((SourceChange("updated", "file-1", candidate("file-1")),), "c1")]
            ),
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider(
                [
                    SourceChangePage(
                        (
                            SourceChange(
                                "updated", "file-1", candidate("file-1", checksum="hash-v2")
                            ),
                        ),
                        "c2",
                    )
                ]
            ),
        )
        self.assertEqual(result.jobs_created, 1)
        self.assertEqual(self.processing.count_jobs(), 2)

    async def test_delete_and_restore_same_content_do_not_reanalyze(self) -> None:
        await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider(
                [SourceChangePage((SourceChange("updated", "file-1", candidate("file-1")),), "c1")]
            ),
        )
        await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider(
                [SourceChangePage((SourceChange("deleted", "file-1"),), "c2")]
            ),
        )
        deleted = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "file-1"
        )
        self.assertIsNotNone(deleted.deleted_at)
        restored = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider(
                [SourceChangePage((SourceChange("restored", "file-1", candidate("file-1")),), "c3")]
            ),
        )
        self.assertIsNone(deleted.deleted_at)
        self.assertEqual(restored.jobs_created, 0)
        self.assertEqual(self.processing.count_jobs(), 1)

    async def test_reconciliation_marks_missing_items_deleted(self) -> None:
        for external_id in ("file-1", "file-2"):
            self.assets.upsert_source_asset(
                tenant_id="tenant-a",
                external_source_id=self.source.id,
                external_asset_id=external_id,
                filename=f"{external_id}.png",
            )
        self.session.commit()
        provider = FakeProvider(
            [SourceChangePage((SourceChange("updated", "file-1", candidate("file-1")),), "done")]
        )
        result = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=provider,
            reconciliation=True,
        )
        missing = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "file-2"
        )
        self.assertTrue(provider.inputs[0].reconciliation)
        self.assertTrue(result.reconciliation)
        self.assertIsNotNone(missing.deleted_at)

    async def test_google_drive_persists_all_items_but_enqueues_supported_images_only(self) -> None:
        mime_types = (
            ("jpeg", "image/jpeg", False),
            ("png", "image/png", False),
            ("webp", "image/webp", False),
            ("avif", "image/avif", False),
            ("heic", "image/heic", False),
            ("heif", "image/heif", False),
            ("folder", "application/vnd.google-apps.folder", True),
            ("shortcut", "application/vnd.google-apps.shortcut", False),
            ("video", "video/mp4", False),
            ("pdf", "application/pdf", False),
            ("document", "application/vnd.google-apps.document", False),
            ("gif", "image/gif", False),
        )
        changes = tuple(
            SourceChange(
                "updated",
                external_id,
                candidate(
                    external_id,
                    name=external_id,
                    mime_type=mime_type,
                    is_folder=is_folder,
                ),
            )
            for external_id, mime_type, is_folder in mime_types
        )

        result = await self.service.sync_source(
            tenant_id="tenant-a",
            source_id=self.source.id,
            provider=FakeProvider([SourceChangePage(changes, "done")]),
        )

        self.assertEqual(result.changes, len(mime_types))
        self.assertEqual(result.jobs_created, 6)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(SourceAssetModel)),
            len(mime_types),
        )
        self.assertEqual(self.processing.count_jobs(), 6)

    async def test_feature_flag_preserves_existing_behavior(self) -> None:
        disabled = SourceSyncService(self.repository, self.processing, enabled=False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            await disabled.sync_source(
                tenant_id="tenant-a",
                source_id=self.source.id,
                provider=FakeProvider([]),
            )


    def _enable_video_enqueue(self):
        from app.core.config import Settings
        from app.modules.video_search.model import VideoMetadataProfileModel
        self.session.add(VideoMetadataProfileModel(tenant_id="tenant-a", profile_name="video", profile_version="v1", prompt_template="describe", active=True))
        self.session.commit()
        self.service = SourceSyncService(self.repository, self.processing, enabled=True, settings=Settings(PROCESSING_JOBS_ENABLED=True, VIDEO_SEARCH_ENABLED=True, VIDEO_ANALYSIS_ENABLED=True, VIDEO_PROXY_ENABLED=True))

    async def test_supported_video_enqueues_metadata_only_and_dedupes(self):
        self._enable_video_enqueue()
        video = candidate("video-1", name="clip.mp4", mime_type="video/mp4")
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "video-1", video),), "c1")]))
        jobs = list(self.session.scalars(select(ProcessingJobModel)))
        self.assertEqual([(job.job_type, job.tenant_id) for job in jobs], [("video_analyze", "tenant-a")])
        self.assertNotIn("ai_model", jobs[0].payload_json)
        self.assertIn("source_fingerprint", jobs[0].payload_json)
        repeated = await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "video-1", video),), "c2")]))
        self.assertEqual(repeated.jobs_created, 0)
        self.assertEqual(self.processing.count_jobs(), 1)

    async def test_changed_deleted_unsupported_and_no_profile_videos_do_not_download(self):
        self._enable_video_enqueue()
        first = candidate("video-2", name="clip.mov", mime_type="video/quicktime", checksum=None)
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "video-2", first),), "c1")]))
        changed = ExternalAssetCandidate(source_type=first.source_type, source_id=first.source_id, external_asset_id=first.external_asset_id, filename=first.filename, mime_type=first.mime_type, source_modified_at="2026-07-19T08:00:00Z", provider_checksum=None, source_metadata=first.source_metadata)
        changed_result = await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "video-2", changed),), "c2")]))
        self.assertEqual(changed_result.jobs_created, 1)
        before = self.processing.count_jobs()
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("deleted", "video-2"),), "c3")]))
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "pdf", candidate("pdf", name="x.pdf", mime_type="application/pdf")),), "c4")]))
        self.assertEqual(self.processing.count_jobs(), before)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProcessingJobModel).where(ProcessingJobModel.job_type == "source_asset_download")), 0)

    async def test_video_without_active_profile_safely_skips_enqueue(self):
        from app.core.config import Settings
        self.service = SourceSyncService(self.repository, self.processing, enabled=True, settings=Settings(PROCESSING_JOBS_ENABLED=True, VIDEO_SEARCH_ENABLED=True, VIDEO_ANALYSIS_ENABLED=True, VIDEO_PROXY_ENABLED=True))
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "video-no-profile", candidate("video-no-profile", name="clip.mp4", mime_type="video/mp4")),), "done")]))
        self.assertEqual(self.processing.count_jobs(), 0)


    async def test_restored_video_same_fingerprint_does_not_duplicate_job(self):
        self._enable_video_enqueue()
        video = candidate("restore-same", name="clip.mp4", mime_type="video/mp4")
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "restore-same", video),), "c1")]))
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("deleted", "restore-same"),), "c2")]))
        result = await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("restored", "restore-same", video),), "c3")]))
        self.assertEqual((result.jobs_created, self.processing.count_jobs()), (0, 1))

    async def test_restored_video_changed_fingerprint_enqueues_once(self):
        self._enable_video_enqueue()
        first = candidate("restore-change", name="clip.mp4", mime_type="video/mp4", checksum="v1")
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("updated", "restore-change", first),), "c1")]))
        await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("deleted", "restore-change"),), "c2")]))
        changed = candidate("restore-change", name="clip.mp4", mime_type="video/mp4", checksum="v2")
        result = await self.service.sync_source(tenant_id="tenant-a", source_id=self.source.id, provider=FakeProvider([SourceChangePage((SourceChange("restored", "restore-change", changed),), "c3")]))
        self.assertEqual((result.jobs_created, self.processing.count_jobs()), (1, 2))
