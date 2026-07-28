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
from app.modules.search.router import _suggestion_values
from app.modules.search.runtime import API_SEARCH_INDEX_POOL, SEARCH_SUGGESTION_CACHE


class SearchV2ApiTest(unittest.TestCase):
    def setUp(self):
        API_SEARCH_INDEX_POOL.clear()
        SEARCH_SUGGESTION_CACHE.clear()
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
        API_SEARCH_INDEX_POOL.clear()
        SEARCH_SUGGESTION_CACHE.clear()

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

            async def aclose(self):
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
            patch("app.modules.search.runtime.ElasticsearchV2Index", FakeIndex),
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
        self.assertFalse(captured[0]["track_total_hits"])
        self.assertEqual(captured[0]["size"], 14)
        self.assertEqual(captured[0]["terminate_after"], 100)
        self.assertEqual(captured[0]["timeout"], "300ms")

    def test_suggestions_cache_is_tenant_and_provider_scoped(self):
        class FakeIndex:
            calls = 0
            instances = 0

            def __init__(self, *_args, **_kwargs):
                type(self).instances += 1

            async def aclose(self):
                return None

            async def search(self, _query):
                type(self).calls += 1
                return {"took": 1, "hits": {"hits": []}}

        settings = Settings(
            SEARCH_V3_ENABLED=True,
            SEARCH_QUERY_PARSER_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://search.test:9200",
        )
        with (
            patch("app.modules.search.router.SessionLocal", self.factory),
            patch("app.modules.search.router.get_settings", return_value=settings),
            patch("app.modules.search.router.enabled", return_value=True),
            patch("app.modules.search.runtime.ElasticsearchV2Index", FakeIndex),
        ):
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=sharepoint").status_code, 200)
        self.assertEqual(FakeIndex.calls, 2)

    def test_suggestion_values_prefer_exact_and_compact_completions(self):
        values = _suggestion_values({
            "visible_text": "nurse sweatshirt overhead soft natural product photography",
            "search_terms": ["nurse"],
        }, "nurse")
        self.assertEqual(values[0], ("search_text", "nurse", ""))
        self.assertIn(("visible_text", "nurse sweatshirt", " sweatshirt"), values)
        self.assertNotIn(("visible_text", "nurse sweatshirt overhead soft natural product photography", " sweatshirt overhead soft natural product photography"), values)

    def test_suggestions_require_at_least_two_characters(self):
        response = self.client.get("/api/v1/search/suggestions?q=m")
        self.assertEqual(response.status_code, 422)

    def test_search_requires_authentication(self):
        app.dependency_overrides.clear()
        response = self.client.post("/api/v1/search", json={"query": "cat"})
        self.assertEqual(response.status_code, 401)


class ApiSearchIndexPoolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        API_SEARCH_INDEX_POOL.clear()

    async def asyncTearDown(self) -> None:
        await API_SEARCH_INDEX_POOL.aclose_current_loop()

    async def test_reuses_one_index_and_closes_it_on_shutdown(self):
        from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config

        class FakeIndex:
            instances = 0
            closes = 0

            def __init__(self, _config):
                type(self).instances += 1

            async def aclose(self):
                type(self).closes += 1

        with patch("app.modules.search.runtime.ElasticsearchV2Index", FakeIndex):
            config = ElasticsearchV2Config("http://search.test:9200", index_generation="v3")
            first = await API_SEARCH_INDEX_POOL.get(config)
            second = await API_SEARCH_INDEX_POOL.get(config)
            self.assertIs(first, second)
            self.assertEqual(FakeIndex.instances, 1)
        await API_SEARCH_INDEX_POOL.aclose_current_loop()
        self.assertEqual(FakeIndex.closes, 1)


if __name__ == "__main__":
    unittest.main()
