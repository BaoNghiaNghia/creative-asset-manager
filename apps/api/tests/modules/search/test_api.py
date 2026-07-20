import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_metadata.model import MetadataProfileModel

class SearchV2ApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            session.add(MetadataProfileModel(tenant_id="tenant-a", profile_name="general", profile_version="1", prompt_template="Analyze", search_config_json={"facet_paths": ["subject"]}))
            session.commit()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.engine.dispose()

    def test_capabilities_preserve_v1_when_rollout_is_disabled(self):
        with patch("app.modules.search.router.SessionLocal", self.factory), patch("app.modules.asset_details.router.get_google_session", return_value=SimpleNamespace(user={"id": "tenant-a"})), patch("app.modules.search.router.get_settings", return_value=Settings()):
            response = self.client.get("/api/v1/search/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["selected_version"], "v1")
        self.assertEqual(response.json()["facet_names"], ["subject"])
        self.assertFalse(response.json()["debug_allowed"])

    def test_search_requires_authentication(self):
        response = self.client.post("/api/v1/search", json={"query": "cat"})
        self.assertEqual(response.status_code, 401)

if __name__ == "__main__":
    unittest.main()
