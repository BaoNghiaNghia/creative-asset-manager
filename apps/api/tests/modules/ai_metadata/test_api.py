import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel


class AssetAnalysisAdminApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        with self.factory() as session:
            asset = AssetModel(tenant_id="tenant-a", content_hash="b" * 64)
            session.add(asset)
            session.flush()
            AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze",
            )
            session.commit()
            self.asset_id = asset.id
        self.client = TestClient(app)
        self.settings = Settings(
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            GEMINI_API_KEY="test-only",
        )

    def tearDown(self):
        self.engine.dispose()

    def test_authenticated_enqueue_is_async_and_idempotent(self):
        body = {
            "asset_id": self.asset_id,
            "metadata_profile": "general",
            "source_provider": "google-drive",
        }
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.ai_metadata.router.get_google_session",
                return_value=SimpleNamespace(user={"id": "tenant-a"}),
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=self.settings,
            ),
        ):
            first = self.client.post("/api/v1/admin/asset-analyses", json=body)
            second = self.client.post("/api/v1/admin/asset-analyses", json=body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["analysis_id"], second.json()["analysis_id"])
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 1
            )

    def test_force_creates_new_history_and_job(self):
        body = {
            "asset_id": self.asset_id,
            "metadata_profile": "general",
            "force": True,
        }
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.ai_metadata.router.get_google_session",
                return_value=SimpleNamespace(user={"id": "tenant-a"}),
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=self.settings,
            ),
        ):
            first = self.client.post("/api/v1/admin/asset-analyses", json=body)
            second = self.client.post("/api/v1/admin/asset-analyses", json=body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(first.json()["analysis_id"], second.json()["analysis_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2
            )

    def test_unauthenticated_and_disabled_requests_are_rejected(self):
        body = {"asset_id": self.asset_id, "metadata_profile": "general"}
        with (
            patch("app.modules.ai_metadata.router.get_settings", return_value=self.settings),
            patch("app.modules.ai_metadata.router.get_google_session", return_value=None),
        ):
            self.assertEqual(
                self.client.post("/api/v1/admin/asset-analyses", json=body).status_code,
                401,
            )
        with patch(
            "app.modules.ai_metadata.router.get_settings",
            return_value=Settings(),
        ):
            self.assertEqual(
                self.client.post("/api/v1/admin/asset-analyses", json=body).status_code,
                404,
            )
