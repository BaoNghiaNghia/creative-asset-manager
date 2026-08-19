import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.core.config import Settings
from app.modules.authorization.principal import CurrentPrincipal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
from app.modules.video_search.router import VideoSearchRequest, video_search
from app.modules.video_search.search import build_video_search_query

URL = os.getenv("INTEGRATION_ELASTICSEARCH_URL", "")


@unittest.skipUnless(URL.startswith("http"), "real Elasticsearch required")
class VideoSearchElasticsearchIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prefix = "cam-video-search-" + uuid4().hex[:10]
        self.settings = Settings(
            VIDEO_SEARCH_ENABLED=True,
            SEARCH_V3_ENABLED=True,
            ELASTICSEARCH_URL=URL,
            ELASTICSEARCH_INDEX_PREFIX=self.prefix,
        )
        self.index = VideoSearchElasticsearchIndex(
            ElasticsearchV3Config(URL, index_prefix=self.prefix, index_generation="v3")
        )
        self.physical = await self.index.create_index("000001")
        await self.index.switch_aliases(self.physical)

    async def asyncTearDown(self):
        try:
            await self.index._index._request("DELETE", f"/{self.physical}", allow_not_found=True)
        finally:
            await self.index.aclose()

    def principal(self, tenant):
        return CurrentPrincipal(
            user_id="user-" + tenant, active_tenant_id=tenant, membership_id="member-" + tenant,
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id=None, authorization_source="test",
        )

    def document(self, identifier, tenant, run, segments, *, filename="ride.mp4"):
        return {
            "_id": identifier, "tenant_id": tenant, "source_asset_id": "asset-" + run,
            "source_fingerprint": ("f" * 64), "analysis_run_id": run,
            "video_metadata_profile_id": "profile-" + tenant,
            "metadata_profile": "video", "metadata_profile_version": "v1",
            "prompt_version": "p1", "analysis_version": "a1", "ai_provider": "gemini",
            "ai_model": "model", "duration_ms": 30000, "summary": "horse riding video",
            "source_type": "google_drive", "external_source_id": "source-" + tenant,
            "external_asset_id": "external-" + run, "filename": filename,
            "mime_type": "video/mp4", "web_url": "https://drive.example/" + run,
            "thumbnail_url": "https://thumb.example/" + run,
            "analysis_completed_at": "2026-01-01T00:00:00+00:00",
            "segments": segments,
        }

    async def test_real_nested_search_ranks_segments_and_enforces_tenant(self):
        await self.index.upsert_video_document(self.document(
            "a-best", "tenant-a", "run-best", [
                {"start_ms": 12000, "end_ms": 18500, "summary": "horse riding through field", "visual_description": "rider on horse", "speech": "", "visible_text": "", "keywords": ["horse", "riding"], "actions": ["riding"], "objects": ["horse"], "people": [], "products": [], "locations": [], "styles": [], "colors": [], "moods": [], "confidence": 0.92},
                {"start_ms": 20000, "end_ms": 23000, "summary": "horse riding continues", "visual_description": "", "speech": "", "visible_text": "", "keywords": ["horse"], "actions": [], "objects": [], "people": [], "products": [], "locations": [], "styles": [], "colors": [], "moods": [], "confidence": 0.8},
            ],
        ))
        await self.index.upsert_video_document(self.document(
            "a-lower", "tenant-a", "run-lower", [
                {"start_ms": 1000, "end_ms": 4000, "summary": "horse appears", "visual_description": "", "speech": "", "visible_text": "", "keywords": ["horse"], "actions": [], "objects": [], "people": [], "products": [], "locations": [], "styles": [], "colors": [], "moods": [], "confidence": 0.7},
            ],
        ))
        await self.index.upsert_video_document(self.document(
            "a-unmatched", "tenant-a", "run-unmatched", [
                {"start_ms": 0, "end_ms": 1000, "summary": "cat sleeping", "visual_description": "", "speech": "", "visible_text": "", "keywords": ["cat"], "actions": [], "objects": [], "people": [], "products": [], "locations": [], "styles": [], "colors": [], "moods": [], "confidence": 0.5},
            ],
        ))
        await self.index.upsert_video_document(self.document(
            "b-foreign", "tenant-b", "run-foreign", [
                {"start_ms": 5000, "end_ms": 9000, "summary": "horse riding foreign", "visual_description": "", "speech": "", "visible_text": "", "keywords": ["horse", "riding"], "actions": [], "objects": [], "people": [], "products": [], "locations": [], "styles": [], "colors": [], "moods": [], "confidence": 0.9},
            ],
        ))

        raw = await self.index.search(build_video_search_query(
            query="horse riding", tenant_id="tenant-a", limit=20,
        ))
        raw_hit = raw["hits"]["hits"][0]
        self.assertNotIn("segments", raw_hit["_source"])
        nested_hits = raw_hit["inner_hits"]["matching_segments"]["hits"]["hits"]
        self.assertTrue(nested_hits)
        segment_ranges = []
        for nested_hit in nested_hits:
            nested_source = nested_hit["_source"]
            if "segments" in nested_source:
                nested_source = nested_source["segments"]
            segment_ranges.append((nested_source["start_ms"], nested_source["end_ms"]))
        self.assertIn((12000, 18500), segment_ranges)

        with patch("app.modules.video_search.router.get_settings", return_value=self.settings):
            result = await video_search(
                VideoSearchRequest(query="horse riding", limit=20),
                self.principal("tenant-a"),
            )

        self.assertEqual(result["total"], 2)
        self.assertEqual([item["analysis_run_id"] for item in result["items"]], ["run-best", "run-lower"])
        best = result["items"][0]["best_match"]
        self.assertEqual((best["start_ms"], best["end_ms"]), (12000, 18500))
        self.assertEqual(len(result["items"][0]["matches"]), 2)
        self.assertEqual(result["items"][0]["web_url"], "https://drive.example/run-best")
        self.assertNotIn("run-foreign", str(result))
        self.assertNotIn("run-unmatched", str(result))


if __name__ == "__main__":
    unittest.main()
