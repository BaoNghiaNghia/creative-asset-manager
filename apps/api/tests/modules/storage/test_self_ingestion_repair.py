from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.external_ingestion.model import (
    AssetIngestionItemModel,
    AssetIngestionModel,
    ExternalApiCredentialModel,
)
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.state import PipelineState
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

    def _add_managed_duplicate(
        self, *, parents: list[str] | None = None, deleted: bool = False
    ) -> tuple[SourceAssetModel, AssetSourceLinkModel]:
        duplicate_source = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key="drive-duplicate",
            source_type="google_drive",
        )
        self.session.add(duplicate_source)
        self.session.flush()
        duplicate = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=duplicate_source.id,
            external_asset_id="managed-drive-id",
            filename="staging-duplicate.png",
            source_metadata={"parents": parents or ["managed-root"]},
        )
        if deleted:
            duplicate.deleted_at = self.now
        self.session.add(duplicate)
        self.session.flush()
        link = AssetSourceLinkModel(
            tenant_id="tenant-a", asset_id=self.asset.id, source_asset_id=duplicate.id
        )
        self.session.add(link)
        self.session.commit()
        return duplicate, link

    def _add_pipeline(
        self, source: SourceAssetModel, *, state: str = PipelineState.COMPLETED.value
    ) -> AssetPipelineModel:
        pipeline = AssetPipelineModel(
            tenant_id="tenant-a",
            correlation_id=f"source_asset:{source.id}",
            origin_type="source_asset",
            origin_id=source.id,
            source_asset_id=source.id,
            asset_id=self.asset.id,
            analysis_id=self.analysis.id,
            state=state,
            content_hash=self.asset.content_hash,
            projection_version="v1",
            projection_checksum="checksum",
            status_data_json={"preserved": True},
            completed_at=self.now - timedelta(minutes=1),
        )
        self.session.add(pipeline)
        self.session.commit()
        return pipeline

    def test_candidate_statement_is_postgresql_safe_and_bounded(self) -> None:
        statement = ManagedStorageSelfIngestionRepairService._candidate_statement(
            tenant_id="tenant-a",
            limit=17,
        )
        sql = " ".join(
            str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            ).split()
        )
        self.assertNotIn("SELECT DISTINCT", sql)
        self.assertIn("EXISTS (SELECT 1", sql)
        self.assertIn(
            "ORDER BY asset_storage_objects.stored_at, asset_storage_objects.id",
            sql,
        )
        self.assertIn("LIMIT 17", sql)

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

    async def test_completed_pipeline_history_is_preserved_and_detached(self) -> None:
        managed_pipeline = self._add_pipeline(self.managed)
        original_pipeline = self._add_pipeline(self.original)
        preserved = {
            "tenant_id": managed_pipeline.tenant_id,
            "asset_id": managed_pipeline.asset_id,
            "analysis_id": managed_pipeline.analysis_id,
            "correlation_id": managed_pipeline.correlation_id,
            "origin_type": managed_pipeline.origin_type,
            "origin_id": managed_pipeline.origin_id,
            "state": managed_pipeline.state,
            "content_hash": managed_pipeline.content_hash,
            "projection_version": managed_pipeline.projection_version,
            "projection_checksum": managed_pipeline.projection_checksum,
            "status_data_json": managed_pipeline.status_data_json,
        }
        created_at = managed_pipeline.created_at
        completed_at = managed_pipeline.completed_at

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)

        self.assertEqual(result.repaired_links, 1)
        self.assertEqual(result.removed_source_assets, 1)
        self.session.expire_all()
        detached = self.session.get(AssetPipelineModel, managed_pipeline.id)
        self.assertIsNotNone(detached)
        assert detached is not None
        self.assertIsNone(detached.source_asset_id)
        self.assertEqual(
            {field: getattr(detached, field) for field in preserved}, preserved
        )
        self.assertIsNotNone(detached.tenant_id)
        self.assertEqual(
            detached.created_at.replace(tzinfo=timezone.utc),
            created_at.replace(tzinfo=timezone.utc),
        )
        self.assertEqual(
            detached.completed_at.replace(tzinfo=timezone.utc),
            completed_at.replace(tzinfo=timezone.utc),
        )
        retained = self.session.get(AssetPipelineModel, original_pipeline.id)
        self.assertIsNotNone(retained)
        assert retained is not None
        self.assertEqual(retained.source_asset_id, self.original.id)

    async def test_restrictive_external_ingestion_reference_blocks_repair(self) -> None:
        credential = ExternalApiCredentialModel(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            name="test",
            key_prefix="test",
            secret_hash="a" * 64,
        )
        self.session.add(credential)
        self.session.flush()
        ingestion = AssetIngestionModel(
            tenant_id="tenant-a",
            external_source_id=self.source.id,
            credential_id=credential.id,
            idempotency_key="repair-guard",
            request_hash="b" * 64,
            request_json={},
            received_count=1,
        )
        self.session.add(ingestion)
        self.session.flush()
        item = AssetIngestionItemModel(
            tenant_id="tenant-a",
            ingestion_id=ingestion.id,
            position=0,
            external_asset_id="managed-drive-id",
            download_url="https://example.test/download",
            status="completed",
            source_asset_id=self.managed.id,
        )
        self.session.add(item)
        self.session.commit()

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)

        self.assertEqual(result.self_ingested, 0)
        self.assertEqual(result.repairable, 0)
        self.assertEqual(result.skipped_ambiguous, 1)
        self.assertEqual(result.repaired_links, 0)
        self.assertEqual(result.removed_source_assets, 0)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(SourceAssetModel, self.managed.id))
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, self.managed_link.id))

    async def test_non_terminal_pipeline_blocks_repair_without_partial_changes(self) -> None:
        pipeline = self._add_pipeline(
            self.managed, state=PipelineState.ANALYSIS_PENDING.value
        )

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)

        self.assertEqual(result.self_ingested, 1)
        self.assertEqual(result.repairable, 0)
        self.assertEqual(result.skipped_ambiguous, 1)
        self.assertEqual(result.repaired_links, 0)
        self.assertEqual(result.removed_source_assets, 0)
        self.session.expire_all()
        self.assertIsNotNone(
            self.session.get(AssetSourceLinkModel, self.managed_link.id)
        )
        retained = self.session.get(AssetPipelineModel, pipeline.id)
        self.assertIsNotNone(retained)
        assert retained is not None
        self.assertEqual(retained.source_asset_id, self.managed.id)

    async def test_mutation_counters_are_not_reported_when_commit_fails(self) -> None:
        class FailingCommitSession(Session):
            def commit(self) -> None:
                self.rollback()
                raise RuntimeError("forced commit failure")

        service = ManagedStorageSelfIngestionRepairService(
            lambda: FailingCommitSession(self.engine, expire_on_commit=False),
            Settings(),
        )
        result = await service.execute(tenant_id="tenant-a", dry_run=False)

        self.assertEqual(result.self_ingested, 1)
        self.assertEqual(result.repairable, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.repaired_links, 0)
        self.assertEqual(result.removed_source_assets, 0)
        self.session.expire_all()
        self.assertIsNotNone(
            self.session.get(AssetSourceLinkModel, self.managed_link.id)
        )
        self.assertIsNotNone(self.session.get(SourceAssetModel, self.managed.id))

    async def test_two_managed_sources_are_repaired_atomically_as_one_storage_object(self) -> None:
        duplicate, duplicate_link = self._add_managed_duplicate()
        asset_id, original_id, managed_id, duplicate_id, storage_id, analysis_id = (
            self.asset.id, self.original.id, self.managed.id, duplicate.id,
            self.storage.id, self.analysis.id,
        )
        duplicate_link_id = duplicate_link.id
        preview = await self._repair().execute(tenant_id="tenant-a", dry_run=True)
        self.assertEqual(preview.selected, 1)
        self.assertEqual(preview.self_ingested, 1)
        self.assertEqual(preview.repairable, 1)
        self.assertEqual(preview.repaired_links, 0)
        self.assertEqual(preview.removed_source_assets, 0)
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, duplicate_link_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, duplicate_id))

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.self_ingested, 1)
        self.assertEqual(result.repairable, 1)
        self.assertEqual(result.repaired_links, 2)
        self.assertEqual(result.removed_source_assets, 2)
        self.session.expire_all()
        self.assertIsNone(self.session.get(SourceAssetModel, managed_id))
        self.assertIsNone(self.session.get(SourceAssetModel, duplicate_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, original_id))
        self.assertIsNotNone(self.session.get(AssetModel, asset_id))
        self.assertIsNotNone(self.session.get(AssetStorageObjectModel, storage_id))
        self.assertIsNotNone(self.session.get(type(self.analysis), analysis_id))

    async def test_active_and_deleted_managed_duplicates_are_both_repaired(self) -> None:
        duplicate, _ = self._add_managed_duplicate(deleted=True)
        managed_id, duplicate_id, original_id = self.managed.id, duplicate.id, self.original.id
        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.repairable, 1)
        self.assertEqual(result.repaired_links, 2)
        self.assertEqual(result.removed_source_assets, 2)
        self.session.expire_all()
        self.assertIsNone(self.session.get(SourceAssetModel, managed_id))
        self.assertIsNone(self.session.get(SourceAssetModel, duplicate_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, original_id))

    async def test_mixed_managed_and_non_managed_duplicates_are_not_partially_repaired(self) -> None:
        duplicate, duplicate_link = self._add_managed_duplicate(parents=["customer-folder"])
        managed_link_id, duplicate_link_id, managed_id, duplicate_id = (
            self.managed_link.id, duplicate_link.id, self.managed.id, duplicate.id
        )
        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.self_ingested, 0)
        self.assertEqual(result.skipped_ambiguous, 1)
        self.assertEqual(result.repaired_links, 0)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, managed_link_id))
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, duplicate_link_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, managed_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, duplicate_id))

    async def test_duplicate_with_multiple_links_is_not_partially_repaired(self) -> None:
        duplicate, duplicate_link = self._add_managed_duplicate()
        other_asset = AssetModel(tenant_id="tenant-a", content_hash="b" * 64)
        self.session.add(other_asset)
        self.session.flush()
        cross_link = AssetSourceLinkModel(
            tenant_id="tenant-a", asset_id=other_asset.id, source_asset_id=duplicate.id
        )
        self.session.add(cross_link)
        self.session.commit()
        managed_link_id, duplicate_link_id, cross_link_id = (
            self.managed_link.id, duplicate_link.id, cross_link.id
        )

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.skipped_ambiguous, 1)
        self.assertEqual(result.repaired_links, 0)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, managed_link_id))
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, duplicate_link_id))
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, cross_link_id))

    async def test_duplicate_managed_sources_without_an_original_source_are_not_repaired(self) -> None:
        duplicate, duplicate_link = self._add_managed_duplicate()
        self.session.delete(self.original_link)
        self.session.delete(self.original)
        self.session.commit()
        managed_link_id, duplicate_link_id, managed_id, duplicate_id = (
            self.managed_link.id, duplicate_link.id, self.managed.id, duplicate.id
        )

        result = await self._repair().execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.self_ingested, 1)
        self.assertEqual(result.skipped_only_source, 1)
        self.assertEqual(result.repaired_links, 0)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, managed_link_id))
        self.assertIsNotNone(self.session.get(AssetSourceLinkModel, duplicate_link_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, managed_id))
        self.assertIsNotNone(self.session.get(SourceAssetModel, duplicate_id))

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
            Settings(
                GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="managed-root",
                MANAGED_STORAGE_COMPLETED_RETENTION_HOURS=6,
            ),
            provider,
        )
        result = await cleanup.execute(tenant_id="tenant-a", dry_run=False)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(provider.deleted, ["managed-drive-id"])
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(SourceAssetModel, original_id))
        self.assertIsNotNone(self.session.get(AssetModel, asset_id))
        self.assertIsNone(self.session.get(AssetStorageObjectModel, storage_id))
