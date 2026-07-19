import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)


class AssetRegistryRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.repository = AssetRegistryRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _source(self, tenant: str, key: str):
        return self.repository.upsert_external_source(
            tenant_id=tenant,
            source_key=key,
            source_type="google_drive",
        )

    def _source_asset(self, tenant: str, source_id: str, external_id: str):
        return self.repository.upsert_source_asset(
            tenant_id=tenant,
            external_source_id=source_id,
            external_asset_id=external_id,
            filename="asset.png",
        )

    def test_two_tenants_can_store_the_same_content_hash(self) -> None:
        first = self.repository.create_asset(tenant_id="tenant-a", content_hash="a" * 64)
        second = self.repository.create_asset(tenant_id="tenant-b", content_hash="a" * 64)
        self.session.commit()

        self.assertNotEqual(first.id, second.id)

    def test_same_tenant_cannot_create_duplicate_content_hash(self) -> None:
        self.repository.create_asset(tenant_id="tenant-a", content_hash="a" * 64)
        with self.assertRaises(AssetContentConflictError):
            self.repository.create_asset(tenant_id="tenant-a", content_hash="a" * 64)

    def test_same_external_id_in_different_sources_is_valid(self) -> None:
        first_source = self._source("tenant-a", "drive-primary")
        second_source = self._source("tenant-a", "drive-secondary")
        first = self._source_asset("tenant-a", first_source.id, "external-1")
        second = self._source_asset("tenant-a", second_source.id, "external-1")

        self.assertNotEqual(first.id, second.id)

    def test_upsert_same_source_identity_does_not_duplicate(self) -> None:
        source = self._source("tenant-a", "drive-primary")
        first = self._source_asset("tenant-a", source.id, "external-1")
        second = self.repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id=source.id,
            external_asset_id="external-1",
            filename="renamed.png",
        )

        self.assertEqual(first.id, second.id)
        count = self.session.scalar(select(func.count()).select_from(SourceAssetModel))
        self.assertEqual(count, 1)

    def test_duplicate_link_returns_existing_link(self) -> None:
        source = self._source("tenant-a", "drive-primary")
        source_asset = self._source_asset("tenant-a", source.id, "external-1")
        asset = self.repository.create_asset(tenant_id="tenant-a", content_hash="b" * 64)
        first = self.repository.link_source_asset(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id
        )
        second = self.repository.link_source_asset(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id
        )

        self.assertEqual(first.id, second.id)
        count = self.session.scalar(select(func.count()).select_from(AssetSourceLinkModel))
        self.assertEqual(count, 1)

    def test_soft_delete_source_asset_keeps_asset_content(self) -> None:
        source = self._source("tenant-a", "drive-primary")
        source_asset = self._source_asset("tenant-a", source.id, "external-1")
        asset = self.repository.create_asset(tenant_id="tenant-a", content_hash="c" * 64)
        self.repository.link_source_asset(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id
        )

        deleted = self.repository.mark_source_asset_deleted(
            tenant_id="tenant-a", source_asset_id=source_asset.id
        )

        self.assertIsNotNone(deleted.deleted_at)
        self.assertIsNotNone(self.session.get(AssetModel, asset.id))

    def test_sync_cursor_is_updated_in_place(self) -> None:
        source = self._source("tenant-a", "drive-primary")
        first = self.repository.save_sync_cursor(
            tenant_id="tenant-a", external_source_id=source.id, cursor_value="one"
        )
        second = self.repository.save_sync_cursor(
            tenant_id="tenant-a", external_source_id=source.id, cursor_value="two"
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.cursor_value, "two")
