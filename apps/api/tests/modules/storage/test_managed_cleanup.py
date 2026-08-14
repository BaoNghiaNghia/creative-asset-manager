from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.providers.contracts import StorageProviderError, StoreAssetInput, StoredAsset
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.storage.managed_cleanup import ManagedStorageCleanupService
from app.modules.storage.repository import ManagedStorageRepository
from app.modules.storage.service import ManagedAssetStorageService
from app.modules.storage.model import AssetStorageObjectModel


class FakeManagedStorage:
    provider_name = "google_drive_managed"

    def __init__(self, error: StorageProviderError | None = None):
        self.error = error
        self.deleted: list[str] = []

    async def delete_asset(self, input):
        if self.error:
            raise self.error
        self.deleted.append(input.remote_file_id)


class ManagedStorageCleanupServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.assets = AssetRegistryRepository(self.session)
        self.metadata = AiMetadataRepository(self.session)
        self.asset = self.assets.create_asset(tenant_id="tenant-a", content_hash="a" * 64, mime_type="image/png")
        self.profile = self.metadata.create_profile(tenant_id="tenant-a", profile_name="default", profile_version="1", prompt_template="test")
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _record(self, *, age_hours: int = 8) -> AssetStorageObjectModel:
        now = datetime.now(timezone.utc)
        row = AssetStorageObjectModel(
            tenant_id="tenant-a", asset_id=self.asset.id, content_hash=self.asset.content_hash,
            storage_provider="google_drive_managed", status="stored", remote_file_id="managed-only-id",
            remote_folder_id="managed-root", stored_at=now - timedelta(hours=age_hours),
        )
        self.session.add(row)
        self.session.commit()
        return row

    def _analysis(self, *, status: str = "completed", completed_age_hours: int = 7, retryable: bool | None = None):
        analysis = self.metadata.create_analysis(
            tenant_id="tenant-a", asset_id=self.asset.id, metadata_profile_id=self.profile.id,
            prompt_version="1", pipeline_version="1",
        )
        analysis.status = status
        analysis.completed_at = datetime.now(timezone.utc) - timedelta(hours=completed_age_hours)
        analysis.failure_retryable = retryable
        self.session.commit()
        return analysis

    def _service(self, provider):
        return ManagedStorageCleanupService(
            lambda: Session(self.engine, expire_on_commit=False),
            Settings(MANAGED_STORAGE_COMPLETED_RETENTION_HOURS=6, MANAGED_STORAGE_FAILED_RETENTION_HOURS=24),
            provider,
        )

    async def test_completed_staging_is_deleted_but_asset_and_analysis_are_preserved(self) -> None:
        row = self._record()
        row_id = row.id
        analysis = self._analysis()
        provider = FakeManagedStorage()
        result = await self._service(provider).execute(tenant_id="tenant-a")
        self.assertEqual(result.deleted, 1)
        self.assertEqual(provider.deleted, ["managed-only-id"])
        self.session.expire_all()
        self.assertIsNone(self.session.get(AssetStorageObjectModel, row_id))
        self.assertIsNotNone(self.assets.session.get(type(self.asset), self.asset.id))
        self.assertIsNotNone(self.session.get(type(analysis), analysis.id))

    async def test_active_and_budget_blocked_analyses_protect_staging(self) -> None:
        row = self._record()
        row_id = row.id
        self._analysis(status="pending")
        provider = FakeManagedStorage()
        result = await self._service(provider).execute(tenant_id="tenant-a")
        self.assertEqual(result.skipped_active, 1)
        self.assertEqual(provider.deleted, [])
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, row_id))

    async def test_dry_run_and_remote_missing_remove_no_remote_or_db_until_real_run(self) -> None:
        row = self._record()
        row_id = row.id
        self._analysis()
        provider = FakeManagedStorage()
        preview = await self._service(provider).execute(tenant_id="tenant-a", dry_run=True)
        self.assertEqual(preview.eligible, 1)
        self.assertEqual(provider.deleted, [])
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, row_id))
        missing = FakeManagedStorage(StorageProviderError("missing", code="managed_storage_object_missing", retryable=False, status_code=404))
        result = await self._service(missing).execute(tenant_id="tenant-a")
        self.assertEqual(result.already_missing, 1)
        self.session.expire_all()
        self.assertIsNone(self.session.get(AssetStorageObjectModel, row_id))

    async def test_retryable_failed_analysis_protects_staging(self) -> None:
        row = self._record()
        self._analysis(status="failed", retryable=True)
        provider = FakeManagedStorage()
        result = await self._service(provider).execute(tenant_id="tenant-a")
        self.assertEqual(result.skipped_active, 1)
        self.assertEqual(provider.deleted, [])
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, row.id))

    async def test_reanalysis_after_purge_creates_a_fresh_staging_copy(self) -> None:
        self._record()
        self._analysis()
        await self._service(FakeManagedStorage()).execute(tenant_id="tenant-a")

        class FreshUpload:
            provider_name = "google_drive_managed"
            calls = 0
            async def store_asset(self, input):
                self.calls += 1
                return StoredAsset(
                    storage_key="google-drive:fresh-managed-id",
                    content_hash=input.content_hash,
                    storage_provider=self.provider_name,
                    remote_file_id="fresh-managed-id",
                    remote_folder_id="managed-root",
                )

        async def content():
            yield b"image"

        provider = FreshUpload()
        result = await ManagedAssetStorageService(
            self.assets, ManagedStorageRepository(self.session), enabled=True
        ).store(StoreAssetInput(
            tenant_id="tenant-a", asset_id=self.asset.id, content_hash=self.asset.content_hash,
            body=content(), content_type="image/png", filename="asset.png",
        ), provider)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.remote_file_id, "fresh-managed-id")

    async def test_retryable_provider_failure_retains_db_record(self) -> None:
        row = self._record()
        row_id = row.id
        self._analysis()
        provider = FakeManagedStorage(StorageProviderError("busy", code="managed_storage_temporarily_unavailable", retryable=True, status_code=429))
        result = await self._service(provider).execute(tenant_id="tenant-a")
        self.assertEqual(result.failed, 1)
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, row_id))
