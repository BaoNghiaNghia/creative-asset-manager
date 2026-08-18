import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_metadata.model import MetadataProfileModel
from app.modules.authorization.folder_scope import ViewerFolderScopeModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.assets.model import ExternalSourceModel
from app.modules.search.router import _live_suggestion_hits, _search_generation, _search_scope_filters, _search_thumbnail_url, _source_pair_rank, _source_provider_filter, _suggestion_values
from app.modules.search.governance_model import SearchIndexRecordModel
from app.modules.search.runtime import API_SEARCH_INDEX_POOL, SEARCH_SUGGESTION_CACHE


class SearchV3ApiTest(unittest.TestCase):
    def setUp(self):
        API_SEARCH_INDEX_POOL.clear()
        SEARCH_SUGGESTION_CACHE.clear()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            session.add(MetadataProfileModel(tenant_id="tenant-a", profile_name="general", profile_version="1", prompt_template="Analyze", search_config_json={"facet_paths": ["subject"]}))
            session.add(SearchIndexRecordModel(
                physical_index_name="creative-assets-v3-1",
                index_prefix="creative-assets",
                index_version="1",
                projection_version="search-projection-v3",
                lifecycle_state="active",
                verification_json={
                    "passed": True,
                    "mapping_fields": [
                        "tenant_id", "source_id", "ancestor_ids", "visible_text",
                        "search_suggest", "filename.normalized",
                    ],
                    "mapping_matches": True,
                    "analyzer_matches": True,
                },
                activated_at=datetime.now(timezone.utc),
            ))
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

    def test_v3_source_provider_filter_uses_source_ids(self):
        with self.factory() as session:
            session.add_all([
                ExternalSourceModel(
                    tenant_id="tenant-a", source_type="google_drive", source_key="drive-a"
                ),
                ExternalSourceModel(
                    tenant_id="tenant-b", source_type="google_drive", source_key="drive-b"
                ),
            ])
            session.commit()
            source_ids = [str(value) for value in session.scalars(
                select(ExternalSourceModel.id).where(ExternalSourceModel.tenant_id == "tenant-a")
            )]
            result = _source_provider_filter(
                session, "tenant-a", "google-drive", generation="v3"
            )
        self.assertEqual(result, {"terms": {"source_id": source_ids}})

    def test_viewer_suggestion_filters_are_source_and_folder_scoped(self):
        with self.factory() as session:
            session.add_all([
                ExternalSourceModel(
                    id="source-a", tenant_id="tenant-a", source_type="google_drive",
                    source_key="drive-a", source_metadata={},
                ),
                ExternalSourceModel(
                    id="source-b", tenant_id="tenant-a", source_type="google_drive",
                    source_key="drive-b", source_metadata={},
                ),
                ViewerFolderScopeModel(
                    tenant_id="tenant-a", tenant_membership_id="membership-a",
                    external_source_id="source-a", folder_external_id="assigned-a",
                    folder_name="Assigned A",
                ),
            ])
            session.commit()
            principal = CurrentPrincipal(
                user_id="viewer-a", active_tenant_id="tenant-a", membership_id="membership-a",
                external_identity=None, effective_roles=frozenset({"viewer"}),
                effective_permissions=frozenset({"search.read"}), platform_admin=False,
                session_id=None, authorization_source="tenant_rbac",
            )
            filters, _scope_key, restricted = _search_scope_filters(
                session, principal, source_provider="google-drive",
                external_source_id="source-a",
            )

        self.assertTrue(restricted)
        self.assertIn({"term": {"tenant_id": "tenant-a"}}, filters)
        self.assertIn({"terms": {"source_id": ["source-a"]}}, filters)
        self.assertIn("assigned-a", str(filters))
        self.assertNotIn("sibling-folder", str(filters))
        self.assertNotIn("source-b", str(filters))

    def test_operator_suggestion_filters_keep_v3_without_viewer_scope(self):
        with self.factory() as session:
            session.add(ExternalSourceModel(
                id="source-a", tenant_id="tenant-a", source_type="google_drive",
                source_key="drive-a", source_metadata={},
            ))
            session.commit()
            principal = CurrentPrincipal(
                user_id="operator-a", active_tenant_id="tenant-a", membership_id="membership-op",
                external_identity=None, effective_roles=frozenset({"operator"}),
                effective_permissions=frozenset({"search.read"}), platform_admin=False,
                session_id=None, authorization_source="tenant_rbac",
            )
            filters, scope_key, restricted = _search_scope_filters(
                session, principal, source_provider="google-drive",
                external_source_id="source-a",
            )

        self.assertFalse(restricted)
        self.assertIsNone(scope_key)
        self.assertEqual(filters, [
            {"term": {"tenant_id": "tenant-a"}},
            {"terms": {"source_id": ["source-a"]}},
        ])

    def test_suggestions_drop_index_hits_without_a_live_source_asset(self):
        hits = [{"_id": "asset-deleted", "_source": {
            "asset_id": "asset-deleted",
            "source_id": "source-a",
            "visible_text": "must not leak",
        }}]
        with self.factory() as session:
            self.assertEqual(_live_suggestion_hits(session, "tenant-a", hits), [])

    def test_capabilities_never_advertise_a_legacy_generation(self):
        with patch("app.modules.search.router.SessionLocal", self.factory), patch("app.modules.search.router.get_settings", return_value=Settings()):
            response = self.client.get("/api/v1/search/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["selected_version"], "v3")
        self.assertEqual(response.json()["readiness"], "unavailable")
        self.assertFalse(response.json()["search_available"])
        self.assertEqual(response.json()["failure_code"], "search_v3_unavailable")
        self.assertEqual(response.json()["facet_names"], ["subject"])

    def test_all_roles_select_v3(self):
        settings = Settings(
            SEARCH_V3_ENABLED=True,
            SEARCH_QUERY_PARSER_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://search.test:9200",
        )
        roles = ("viewer", "operator", "tenant_admin")
        with (
            patch("app.modules.search.router.SessionLocal", self.factory),
            patch("app.modules.search.router.get_settings", return_value=settings),
            patch("app.modules.search.router.enabled", return_value=True),
        ):
            for role in roles:
                app.dependency_overrides[require_authenticated_principal] = lambda role=role: CurrentPrincipal(
                    user_id=f"user-{role}", active_tenant_id="tenant-a", membership_id="membership-a",
                    external_identity=None, effective_roles=frozenset({role}),
                    effective_permissions=frozenset({"search.read"}), platform_admin=False,
                    session_id=None, authorization_source="tenant_rbac",
                )
                payload = self.client.get("/api/v1/search/capabilities").json()
                self.assertEqual(payload["selected_version"], "v3")
            app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
                user_id="platform-admin", active_tenant_id="tenant-a", membership_id="membership-a",
                external_identity=None, effective_roles=frozenset(),
                effective_permissions=frozenset({"search.read"}), platform_admin=True,
                session_id=None, authorization_source="durable_platform_admin",
            )
            self.assertEqual(self.client.get("/api/v1/search/capabilities").json()["selected_version"], "v3")

    def test_missing_governance_row_uses_v3_compatibility_mode(self):
        with self.factory() as session:
            session.query(SearchIndexRecordModel).delete()
            session.commit()
            settings = Settings(
                SEARCH_V3_ENABLED=True,
                SEARCH_QUERY_PARSER_V2_ENABLED=True,
                ELASTICSEARCH_URL="http://search.test:9200",
            )
            with patch("app.modules.search.router.enabled", return_value=True):
                self.assertEqual(_search_generation(session, "tenant-a", settings), "verification_unknown")

    def test_v3_unavailable_returns_structured_503_without_fallback(self):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="operator-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id=None, authorization_source="tenant_rbac",
        )
        with (
            patch("app.modules.search.router.SessionLocal", self.factory),
            patch("app.modules.search.router.get_settings", return_value=Settings()),
        ):
            response = self.client.post("/api/v1/search", json={"query": "cat"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], {
            "code": "search_v3_unavailable",
            "message": "Search V3 is unavailable.",
            "retryable": True,
        })
        self.assertNotIn("fallback_version", response.text)



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
            patch("app.modules.search.runtime.ElasticsearchV3Index", FakeIndex),
        ):
            response = self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive&external_source_id=source-a")
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
            patch("app.modules.search.runtime.ElasticsearchV3Index", FakeIndex),
        ):
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive&external_source_id=source-a").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=google-drive&external_source_id=source-a").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/search/suggestions?q=milo&source_provider=sharepoint&external_source_id=source-b").status_code, 200)
        self.assertEqual(FakeIndex.calls, 2)

    def test_source_pair_rank_prefers_default_recent_live_source(self):
        old = SimpleNamespace(
            id="old-source-asset",
            last_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        current = SimpleNamespace(
            id="current-source-asset",
            last_seen_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        old_external = SimpleNamespace(
            source_metadata={},
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        current_external = SimpleNamespace(
            source_metadata={"is_default": True},
            updated_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )

        ranked = sorted(
            [(old, old_external), (current, current_external)],
            key=lambda pair: _source_pair_rank(*pair),

            reverse=True,
        )
        self.assertIs(ranked[0][0], current)

    def test_search_image_uses_thumbnail_proxy_instead_of_original_media(self):
        thumbnail_url = _search_thumbnail_url(
            provider="google-drive",
            external_asset_id="drive/file id",
            external_source_id="source-id",
            kind="image",
        )

        self.assertEqual(
            thumbnail_url,
            "/api/explorer/thumbnail/drive%2Ffile%20id"
            "?provider=google-drive&external_source_id=source-id",
        )
        self.assertNotIn("/api/explorer/media/", thumbnail_url)

    def test_search_thumbnail_is_not_emitted_for_unsupported_items(self):
        self.assertIsNone(
            _search_thumbnail_url(
                provider="google-drive",
                external_asset_id="document-id",
                external_source_id="source-id",
                kind="document",
            )
        )
        self.assertIsNone(
            _search_thumbnail_url(
                provider="sharepoint",
                external_asset_id="image-id",
                external_source_id="source-id",
                kind="image",
            )
        )

    def test_suggestion_values_return_compact_completions_without_echoing_exact_term(self):
        values = _suggestion_values({
            "visible_text": "nurse sweatshirt overhead soft natural product photography",
            "search_terms": ["nurse"],
        }, "nurse")
        self.assertNotIn(("search_text", "nurse", ""), values)
        self.assertIn(("visible_text", "nurse sweatshirt", " sweatshirt"), values)
        self.assertNotIn(("visible_text", "nurse sweatshirt overhead soft natural product photography", " sweatshirt overhead soft natural product photography"), values)

    def test_suggestion_values_do_not_block_search_suggest_for_exact_indexed_term(self):
        values = _suggestion_values({
            "search_terms": ["petfull"],
            "search_suggest": "petfull embroidered shirt",
        }, "petfull")
        self.assertNotIn(("search_text", "petfull", ""), values)
        self.assertIn(("search_text", "petfull embroidered", " embroidered"), values)

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
        from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config

        class FakeIndex:
            instances = 0
            closes = 0

            def __init__(self, _config):
                type(self).instances += 1

            async def aclose(self):
                type(self).closes += 1

        with patch("app.modules.search.runtime.ElasticsearchV3Index", FakeIndex):
            config = ElasticsearchV3Config("http://search.test:9200", index_generation="v3")
            first = await API_SEARCH_INDEX_POOL.get(config)
            second = await API_SEARCH_INDEX_POOL.get(config)
            self.assertIs(first, second)
            self.assertEqual(FakeIndex.instances, 1)
        await API_SEARCH_INDEX_POOL.aclose_current_loop()
        self.assertEqual(FakeIndex.closes, 1)


if __name__ == "__main__":
    unittest.main()
