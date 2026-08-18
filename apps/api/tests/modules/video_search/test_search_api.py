import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.video_search.search import VideoSearchResponseError
from tests.modules.video_search.test_search import response


class VideoSearchApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id=None, authorization_source="tenant_rbac",
        )
        self.settings = Settings(
            VIDEO_SEARCH_ENABLED=True,
            ELASTICSEARCH_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://elasticsearch.test",
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    def test_video_route_returns_nested_matches_and_safe_playback_metadata(self):
        with (
            patch("app.modules.video_search.router.get_settings", return_value=self.settings),
            patch("app.modules.video_search.router.VideoSearchElasticsearchIndex") as index_type,
            patch("app.modules.video_search.handler.GeminiVideoClient") as gemini_client,
            patch("app.modules.video_search.handler.VideoProxyPreparationService") as proxy_service,
            patch("app.modules.pipeline.stages.ProviderDownloadStage") as download_stage,
        ):
            index_type.return_value.search = AsyncMock(return_value=response())
            index_type.return_value.aclose = AsyncMock()
            result = self.client.post("/api/v1/search/video", json={"query": "horse riding", "limit": 20})
            gemini_client.assert_not_called()
            proxy_service.assert_not_called()
            download_stage.assert_not_called()
        self.assertEqual(result.status_code, 200)
        item = result.json()["items"][0]
        self.assertEqual(item["best_match"]["start_ms"], 12000)
        self.assertEqual(item["matches"][0]["end_ms"], 18500)
        self.assertEqual(item["web_url"], "https://drive.example/file")
        query = index_type.return_value.search.await_args.args[0]
        self.assertEqual(query["query"]["bool"]["filter"], [{"term": {"tenant_id": "tenant-a"}}])
        index_type.return_value.aclose.assert_awaited_once()

    def test_unavailable_and_malformed_elasticsearch_responses_are_safe(self):
        with (
            patch("app.modules.video_search.router.get_settings", return_value=self.settings),
            patch("app.modules.video_search.router.VideoSearchElasticsearchIndex") as index_type,
        ):
            index_type.return_value.search = AsyncMock(side_effect=__import__("app.infrastructure.search.elasticsearch_v2", fromlist=["ElasticsearchV3RequestError"]).ElasticsearchV3RequestError("offline"))
            index_type.return_value.aclose = AsyncMock()
            unavailable = self.client.post("/api/v1/search/video", json={"query": "horse"})
            self.assertEqual(unavailable.status_code, 503)
            index_type.return_value.search = AsyncMock(return_value={"hits": "bad"})
            malformed = self.client.post("/api/v1/search/video", json={"query": "horse"})
        self.assertEqual(malformed.status_code, 502)
        self.assertNotIn("elasticsearch.test", malformed.text)

    def test_invalid_query_and_disabled_feature_are_rejected(self):
        with patch("app.modules.video_search.router.get_settings", return_value=self.settings):
            invalid = self.client.post("/api/v1/search/video", json={"query": " "})
            self.assertEqual(invalid.status_code, 422)
            invalid_limit = self.client.post("/api/v1/search/video", json={"query": "horse", "limit": 101})
        self.assertEqual(invalid_limit.status_code, 422)
        disabled_settings = Settings(
            VIDEO_SEARCH_ENABLED=False,
            ELASTICSEARCH_V2_ENABLED=True,
            ELASTICSEARCH_URL="http://elasticsearch.test",
        )
        with patch("app.modules.video_search.router.get_settings", return_value=disabled_settings):
            disabled = self.client.post("/api/v1/search/video", json={"query": "horse"})
        self.assertEqual(disabled.status_code, 503)


if __name__ == "__main__":
    unittest.main()
