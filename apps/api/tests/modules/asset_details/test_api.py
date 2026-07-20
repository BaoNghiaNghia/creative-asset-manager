import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
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
            source_asset = SourceAssetModel(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="external-1", filename="safe.png", source_metadata={"access_token": "must-not-leak"})
            session.add(source_asset); session.flush()
            session.add(AssetSourceLinkModel(tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id))
            session.add(AssetStorageObjectModel(tenant_id="tenant-a", asset_id=asset.id, content_hash=asset.content_hash, storage_provider="google_drive", status="stored", remote_file_id="remote-1", web_url="https://user:password@drive.example/file?token=secret"))
            session.commit(); self.asset_id = asset.id
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.engine.dispose()

    def test_details_are_tenant_scoped_and_urls_are_redacted(self):
        with patch("app.modules.asset_details.router.SessionLocal", self.factory), patch("app.modules.asset_details.router.get_google_session", return_value=SimpleNamespace(user={"id": "tenant-a"})):
            response = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sources"][0]["external_source_id"] is not None, True)
        self.assertEqual(payload["storage"][0]["web_url"], "https://drive.example/file")
        self.assertNotIn("secret", response.text)
        with patch("app.modules.asset_details.router.SessionLocal", self.factory), patch("app.modules.asset_details.router.get_google_session", return_value=SimpleNamespace(user={"id": "tenant-b"})):
            denied = self.client.get(f"/api/v1/assets/{self.asset_id}")
        self.assertEqual(denied.status_code, 404)

    def test_unauthenticated_action_is_rejected(self):
        response = self.client.post(f"/api/v1/admin/assets/{self.asset_id}/actions", json={"action": "reindex"})
        self.assertEqual(response.status_code, 401)

if __name__ == "__main__":
    unittest.main()
