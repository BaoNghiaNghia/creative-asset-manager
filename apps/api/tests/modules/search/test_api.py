import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_metadata.model import MetadataProfileModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal

class SearchV2ApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            session.add(MetadataProfileModel(tenant_id="tenant-a", profile_name="general", profile_version="1", prompt_template="Analyze", search_config_json={"facet_paths": ["subject"]}))
            session.commit()
        self.client = TestClient(app)
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id=None, authorization_source="tenant_rbac",
        )

    def tearDown(self):
        app.dependency_overrides.clear(); self.client.close(); self.engine.dispose()

    def test_capabilities_preserve_v1_when_rollout_is_disabled(self):
        with patch("app.modules.search.router.SessionLocal", self.factory), patch("app.modules.search.router.get_settings", return_value=Settings()):
            response = self.client.get("/api/v1/search/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["selected_version"], "v1")
        self.assertEqual(response.json()["facet_names"], ["subject"])
        self.assertFalse(response.json()["debug_allowed"])



    def test_suggestions_use_v3_and_enforce_tenant_filter(self):
        class FakeIndex:
            def __init__(self, *_args, **_kwargs):
                self.query = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def search(self, query):
                self.query = query
                captured.append(query)
                return {
                    "took": 3,
                    "hits": {"hits": [
                        {"_source": {"filename": "milo-bandana.jpg", "visible_text": "Milo's Mom", "search_suggest": "Milo's Mom dog bandana"}},
                        {"_source": {"filename": "other.jpg", "visible_text": "Milo's Mom"}},
                    ]},
                }

        captured = []
        settings = Settings(
            SEARCH_V3_ENABLED=True,
            SEARCH_QUERY_PARSER_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://search.test:9200",
        )
        with (
            patch("app.modules.search.router.SessionLocal", self.factory),
            patch("app.modules.search.router.get_settings", return_value=settings),
            patch("app.modules.search.router.enabled", return_value=True),
            patch("app.modules.search.router.ElasticsearchV2Index", FakeIndex),
        ):
            response = self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["search_version"], "v3")
        self.assertEqual(response.json()["suggestions"], [
            {"text": "Milo's Mom", "prefix": "Milo", "completion": "'s Mom", "kind": "visible_text"},
            {"text": "milo-bandana.jpg", "prefix": "milo", "completion": "-bandana.jpg", "kind": "filename"},
        ])
        self.assertEqual(captured[0]["query"]["bool"]["filter"][0], {"term": {"tenant_id": "tenant-a"}})
        self.assertIn("search_suggest._3gram", str(captured[0]))

    def test_suggestions_require_at_least_two_characters(self):
        response = self.client.get("/api/v1/search/suggestions?q=m")
        self.assertEqual(response.status_code, 422)

    def test_search_requires_authentication(self):
        app.dependency_overrides.clear()
        response = self.client.post("/api/v1/search", json={"query": "cat"})
        self.assertEqual(response.status_code, 401)

if __name__ == "__main__":
    unittest.main()
