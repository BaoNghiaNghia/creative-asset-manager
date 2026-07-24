import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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
        self.assertEqual(result.jobs_created, 3)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(SourceAssetModel)),
            len(mime_types),
        )
        self.assertEqual(self.processing.count_jobs(), 3)

    async def test_feature_flag_preserves_existing_behavior(self) -> None:
        disabled = SourceSyncService(self.repository, self.processing, enabled=False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            await disabled.sync_source(
                tenant_id="tenant-a",
                source_id=self.source.id,
                provider=FakeProvider([]),
            )
