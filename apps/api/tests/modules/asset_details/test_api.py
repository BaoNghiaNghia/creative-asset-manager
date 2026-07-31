import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.authorization.folder_scope import ViewerFolderScopeModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.storage.model import AssetStorageObjectModel

class AssetDetailsApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            source = ExternalSourceModel(tenant_id="tenant-a", source_key="drive-a", source_type="google_drive")
            asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64, mime_type="image/png")
            session.add_all([source, asset]); session.flush()
            source_asset = SourceAssetModel(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="external-1", filename="safe.png", mime_type="image/png", source_metadata={"access_token": "must-not-leak"})
            session.add(source_asset); session.flush()
            session.add(AssetSourceLinkModel(tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id))
            session.add(AssetStorageObjectModel(tenant_id="tenant-a", asset_id=asset.id, content_hash=asset.content_hash, storage_provider="google_drive", status="stored", remote_file_id="remote-1", web_url="https://user:password@drive.example/file?token=secret"))
            session.commit(); self.asset_id = asset.id
        self.client = TestClient(app)

    def _principal(self, tenant_id="tenant-a", permissions=("assets.read",), roles=()):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id=tenant_id, membership_id="membership-a",
            external_identity=None, effective_roles=frozenset(roles),
            effective_permissions=frozenset(permissions), platform_admin=False,
            session_id=None, authorization_source="tenant_rbac",
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close(); self.engine.dispose()

    def test_details_are_tenant_scoped_and_urls_are_redacted(self):
        self._principal()
        with patch("app.modules.asset_details.router.SessionLocal", self.factory):
            response = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sources"][0]["external_source_id"] is not None, True)
        self.assertEqual(payload["sources"][0]["preview_url"], f"/api/explorer/media/external-1?provider=google-drive&external_source_id={payload['sources'][0]['external_source_id']}")
        self.assertEqual(payload["storage"][0]["web_url"], "https://drive.example/file")
        self.assertNotIn("secret", response.text)
        self._principal("tenant-b")
        with patch("app.modules.asset_details.router.SessionLocal", self.factory):
            denied = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(denied.status_code, 404)

    def test_viewer_details_preview_requires_an_assigned_folder(self):
        self._principal(roles=("viewer",))
        with patch("app.modules.asset_details.router.SessionLocal", self.factory):
            denied = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(denied.status_code, 403)

        with self.factory() as session:
            source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == "tenant-a"))
            session.add(ViewerFolderScopeModel(
                tenant_id="tenant-a",
                tenant_membership_id="membership-a",
                external_source_id=source.id,
                folder_external_id="external-1",
                folder_name="Assigned folder",
            ))
            session.commit()

        with patch("app.modules.asset_details.router.SessionLocal", self.factory):
            allowed = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(allowed.status_code, 200)
        self.assertIn(f"external_source_id={source.id}", allowed.json()["sources"][0]["preview_url"])

    def test_unauthenticated_action_is_rejected(self):
        app.dependency_overrides.clear()
        response = self.client.post(f"/api/v1/admin/assets/{self.asset_id}/actions", json={"action": "reindex"})
        self.assertEqual(response.status_code, 401)


    def test_preview_uses_internal_asset_mime_type_when_source_mime_is_missing(self):
        self._principal()
        with self.factory() as session:
            source_asset = session.scalar(select(SourceAssetModel).where(SourceAssetModel.external_asset_id == "external-1"))
            source_asset.mime_type = None
            session.commit()
        with patch("app.modules.asset_details.router.SessionLocal", self.factory):
            response = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(response.status_code, 200)
        source = response.json()["sources"][0]
        self.assertEqual(source["mime_type"], "image/png")
        self.assertEqual(
            source["preview_url"],
            f"/api/explorer/media/external-1?provider=google-drive&external_source_id={source['external_source_id']}",
        )
if __name__ == "__main__":
    unittest.main()
