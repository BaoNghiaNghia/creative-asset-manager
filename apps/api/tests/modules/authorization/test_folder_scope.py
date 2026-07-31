import unittest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.folder_scope import ViewerFolderAccess, ViewerFolderScopeModel, ViewerFolderScopeService


class ViewerFolderScopeTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, class_=Session, expire_on_commit=False)()
        self.service = ViewerFolderScopeService(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_selected_folder_allows_descendant_but_not_sibling(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertTrue(access.allows(item_id="folder-a"))
        self.assertTrue(access.allows(item_id="file-1", parent_id="folder-a"))
        self.assertTrue(access.allows(item_id="nested-file", ancestor_ids=["root", "folder-a", "nested"]))
        self.assertFalse(access.allows(item_id="file-2", parent_id="folder-b"))

    def test_viewer_access_is_tenant_and_membership_scoped(self):
        first = self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[{"folder_id": "folder-a", "folder_name": "Allowed"}],
        )
        self.assertEqual([row.folder_external_id for row in first], ["folder-a"])
        self.assertEqual(
            self.service.access(
                tenant_id="tenant-1", membership_id="member-1",
                roles=frozenset({"viewer"}), external_source_id="source-1",
            ).folder_ids,
            frozenset({"folder-a"}),
        )
        self.assertEqual(
            self.service.access(
                tenant_id="tenant-2", membership_id="member-1",
                roles=frozenset({"viewer"}), external_source_id="source-1",
            ).folder_ids,
            frozenset(),
        )

    def test_operator_is_not_restricted(self):
        access = self.service.access(
            tenant_id="tenant-1", membership_id="member-1",
            roles=frozenset({"viewer", "operator"}), external_source_id="source-1",
        )
        self.assertFalse(access.restricted)

    def test_scope_resolves_only_live_assets_under_selected_folder(self):
        source = ExternalSourceModel(id="source-1", tenant_id="tenant-1", source_key="g", source_type="google_drive", source_metadata={})
        asset = AssetModel(id="asset-1", tenant_id="tenant-1", content_hash="hash-1", mime_type="image/jpeg")
        source_asset = SourceAssetModel(
            id="source-asset-1", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="drive-file-1", source_metadata={"parents": ["folder-a"]},
        )
        self.session.add_all([source, asset, source_asset])
        self.session.flush()
        self.session.add(AssetSourceLinkModel(tenant_id="tenant-1", asset_id="asset-1", source_asset_id="source-asset-1"))
        self.session.flush()
        self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[{"folder_id": "folder-a", "folder_name": "Allowed"}],
        )
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertEqual(self.service.allowed_internal_asset_ids(tenant_id="tenant-1", access=access), {"asset-1"})
        source_asset.deleted_at = source_asset.updated_at
        self.assertEqual(self.service.allowed_internal_asset_ids(tenant_id="tenant-1", access=access), set())


if __name__ == "__main__":
    unittest.main()
