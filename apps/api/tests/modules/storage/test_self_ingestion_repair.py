from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.storage.managed_cleanup import ManagedStorageCleanupService
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.self_ingestion_repair import ManagedStorageSelfIngestionRepairService


class _StorageProvider:
    provider_name = "google_drive_managed"

    def __init__(self):
        self.deleted: list[str] = []

    async def delete_asset(self, input):
        self.deleted.append(input.remote_file_id)


class ManagedStorageSelfIngestionRepairTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.now = datetime.now(timezone.utc)
        self.source = ExternalSourceModel(
            tenant_id="tenant-a", source_key="drive-a", source_type="google_drive"
        )
        self.asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
        self.session.add_all([self.source, self.asset])
        self.session.flush()
        self.original = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="original-drive-id",
            filename="original.png",
            source_metadata={"parents": ["customer-folder"]},
        )
        self.managed = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            external_asset_id="managed-drive-id",
            filename="staging.png",
            source_metadata={"parents": ["managed-root"]},
        )
        self.session.add_all([self.original, self.managed])
        self.session.flush()
        self.original_link = AssetSourceLinkModel(
            tenant_id="tenant-a", asset_id=self.asset.id, source_asset_id=self.original.id
        )
        self.managed_link = AssetSourceLinkModel(
            tenant_id="tenant-a", asset_id=self.asset.id, source_asset_id=self.managed.id
        )
        self.storage = AssetStorageObjectModel(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            content_hash=self.asset.content_hash,
            storage_provider="google_drive_managed",
            status="stored",
            remote_file_id="managed-drive-id",
            remote_folder_id="managed-root",
            stored_at=self.now - timedelta(hours=8),
        )
        self.session.add_all([self.original_link, self.managed_link, self.storage])
        profile = AiMetadataRepository(self.session).create_profile(
            tenant_id="tenant-a",
            profile_name="default",
            profile_version="1",
            prompt_template="test",
        )
        self.analysis = AiMetadataRepository(self.session).create_analysis(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            metadata_profile_id=profile.id,
            prompt_version="1",
            pipeline_version="1",
        )
        self.analysis.status = "completed"
        self.analysis.completed_at = self.now - timedelta(hours=7)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _repair(self) -> ManagedStorageSelfIngestionRepairService:
        return ManagedStorageSelfIngestionRepairService(
            lambda: Session(self.engine, expire_on_commit=False), Settings()
        )

    async def test_dry_run_and_actual_repair_preserve_original_asset_analysis_and_storage(self) -> None:
        preview = await self._repair().execute(tenant_id="tenant-a", dry_run=True)
        self.assertEqual(preview.document()["repairable"], 1)
        self.assertEqual(preview.repaired_links, 0)
        self.assertIsNotNone(self.session.get(SourceAssetModel, self.managed.id))

        asset_id, original_id, managed_id, storage_id, analysis_id = (
            self.asset.id, self.original.id, self.managed.id, self.storage.id, self.analysis.id
        )
        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.repaired_links, 1)
        self.assertEqual(result.removed_source_assets, 1)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(AssetModel, asset_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, original_id))
        self.assertIsNone(self.session.get(SourceAssetModel, managed_id))
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, storage_id))
        self.assertIsNotNone(self.session.get(type(self.analysis), analysis_id))

    async def test_only_managed_source_is_not_repaired(self) -> None:
        self.session.delete(self.original_link)
        self.session.delete(self.original)
        self.session.commit()

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.skipped_only_source, 1)
        self.assertEqual(result.repaired_links, 0)
        self.assertIsNotNone(self.session.get(SourceAssetModel, self.managed.id))

    async def test_wrong_remote_id_tenant_or_root_evidence_is_untouched(self) -> None:
        self.storage.remote_file_id = "different-id"
        self.session.commit()
        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.selected, 0)
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, self.managed_link.id))

        self.storage.remote_file_id = "managed-drive-id"
        self.managed.source_metadata = {"parents": ["unrelated-root"]}
        self.session.commit()
        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.skipped_ambiguous, 1)
        self.assertIsNotNone(self.session.get(SourceAssetModel, self.managed.id))

    async def test_repair_then_cleanup_removes_only_staging_object(self) -> None:
        original_id, asset_id, storage_id = self.original.id, self.asset.id, self.storage.id
        await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        provider = _StorageProvider()
        cleanup = ManagedStorageCleanupService(
            lambda: Session(self.engine, expire_on_commit=False),
            Settings(MANAGED_STORAGE_COMPLETED_RETENTION_HOURS=6),
            provider,
        )
        result = await cleanup.execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(provider.deleted, ["managed-drive-id"])
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(SourceAssetModel, original_id))
        self.assertIsNotNone(self.session.get(AssetModel, asset_id))
        self.assertIsNone(self.session.get(AssetStorageObjectModel, storage_id))
