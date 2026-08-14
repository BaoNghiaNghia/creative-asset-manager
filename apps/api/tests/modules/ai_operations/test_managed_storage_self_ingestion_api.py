from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.storage.model import AssetStorageObjectModel


class ManagedStorageSelfIngestionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            source = ExternalSourceModel(
                tenant_id="tenant-a", source_key="drive", source_type="google_drive"
            )
            asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
            session.add_all([source, asset])
            session.flush()
            original = SourceAssetModel(
                tenant_id="tenant-a", external_source_id=source.id,
                external_asset_id="original", source_metadata={"parents": ["customer-root"]},
            )
            managed = SourceAssetModel(
                tenant_id="tenant-a", external_source_id=source.id,
                external_asset_id="managed-file", source_metadata={"parents": ["managed-root"]},
            )
            session.add_all([original, managed])
            session.flush()
            session.add_all([
                AssetSourceLinkModel(tenant_id="tenant-a", asset_id=asset.id, source_asset_id=original.id),
                AssetSourceLinkModel(tenant_id="tenant-a", asset_id=asset.id, source_asset_id=managed.id),
                AssetStorageObjectModel(
                    tenant_id="tenant-a", asset_id=asset.id, content_hash=asset.content_hash,
                    storage_provider="google_drive_managed", status="stored",
                    remote_file_id="managed-file", remote_folder_id="managed-root",
                ),
            ])
            self.asset_id, self.managed_id = asset.id, managed.id
            session.commit()
        self.client = TestClient(app)
        self._set_permissions({"ai_provider.configure"})

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _set_permissions(self, permissions: set[str]) -> None:
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="admin-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset(permissions), platform_admin=False,
            session_id=None, authorization_source="tenant_rbac",
        )

    def _get(self, path: str):
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory):
            return self.client.get(path)

    def _post(self, path: str, body: dict):
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory):
            return self.client.post(path, json=body)

    def test_preview_is_tenant_scoped_and_actual_repair_only_changes_registry(self) -> None:
        preview = self._get("/api/v1/admin/ai-operations/managed-storage/self-ingestion/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["repairable"], 1)

        actual = self._post(
            "/api/v1/admin/ai-operations/managed-storage/self-ingestion/repair",
            {"dry_run": False, "limit": 100},
        )
        self.assertEqual(actual.status_code, 200)
        self.assertEqual(actual.json()["repaired_links"], 1)
        with self.factory() as session:
            self.assertIsNotNone(session.get(AssetModel, self.asset_id))
            self.assertIsNone(session.get(SourceAssetModel, self.managed_id))
            self.assertIsNotNone(session.scalar(select(AssetStorageObjectModel)))

    def test_repair_requires_strong_admin_permission(self) -> None:
        self._set_permissions(set())
        response = self._post(
            "/api/v1/admin/ai-operations/managed-storage/self-ingestion/repair",
            {"dry_run": True},
        )
        self.assertEqual(response.status_code, 403)
