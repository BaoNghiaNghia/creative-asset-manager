import asyncio
import unittest
from unittest.mock import AsyncMock

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
from app.modules.video_search.search import (
    build_video_search_query,
    parse_video_search_response,
)


def response(*, tenant="tenant-a", segments=None, score=10.0):
    segments = segments or [
        {"_score": 4.0, "_source": {"start_ms": 12000, "end_ms": 18500, "summary": "horse riding", "visual_description": "rider", "speech": "", "confidence": 0.92}},
    ]
    return {
        "took": 2,
        "hits": {
            "total": {"value": 1},
            "hits": [{
                "_score": score,
                "_source": {
                    "tenant_id": tenant, "source_asset_id": "asset-a",
                    "analysis_run_id": "run-a", "filename": "ride.mp4",
                    "mime_type": "video/mp4", "duration_ms": 30000,
                    "source_type": "google_drive", "external_source_id": "source-a",
                    "external_asset_id": "external-a", "web_url": "https://drive.example/file",
                    "thumbnail_url": "https://thumb.example/file",
                },
                "inner_hits": {"matching_segments": {"hits": {"hits": segments}}},
            }],
        },
    }


class VideoSearchQueryTest(unittest.TestCase):
    def test_nested_query_uses_video_fields_and_tenant_filter(self):
        query = build_video_search_query(
            query="horse riding", tenant_id="tenant-a", limit=20
        )
        self.assertEqual(query["query"]["bool"]["filter"], [{"term": {"tenant_id": "tenant-a"}}])
        nested = query["query"]["bool"]["must"][0]["nested"]
        self.assertEqual(nested["path"], "segments")
        self.assertEqual(nested["inner_hits"]["name"], "matching_segments")
        self.assertIn("segments.speech^3", nested["query"]["multi_match"]["fields"])
        self.assertIn("segments.keywords^2", nested["query"]["multi_match"]["fields"])

    def test_query_limits_top_level_source_and_preserves_inner_hits(self):
        query = build_video_search_query(query="horse riding", tenant_id="tenant-a", limit=20)
        self.assertEqual(
            query["_source"]["includes"],
            [
                "source_asset_id", "analysis_run_id", "filename", "mime_type",
                "duration_ms", "source_type", "external_source_id",
                "external_asset_id", "web_url", "thumbnail_url",
            ],
        )
        self.assertNotIn("segments", query["_source"]["includes"])
        nested = query["query"]["bool"]["must"][0]["nested"]
        inner_source = nested["inner_hits"]["_source"]["includes"]
        for field in (
            "segments.start_ms", "segments.end_ms", "segments.summary",
            "segments.visual_description", "segments.speech", "segments.confidence",
            "segments.keywords",
        ):
            self.assertIn(field, inner_source)

    def test_best_match_uses_score_then_timestamp_tie_break_and_preserves_ranges(self):
        result = parse_video_search_response(response(segments=[
            {"_score": 6.0, "_source": {"start_ms": 200, "end_ms": 300, "summary": "later", "visual_description": "", "speech": "", "confidence": 0.5}},
            {"_score": 6.0, "_source": {"start_ms": 100, "end_ms": 250, "summary": "earlier", "visual_description": "", "speech": "", "confidence": 0.6}},
            {"_score": 5.0, "_source": {"start_ms": 0, "end_ms": 50, "summary": "lower", "visual_description": "", "speech": "", "confidence": 0.7}},
        ]))
        item = result["items"][0]
        self.assertEqual((item["best_match"]["start_ms"], item["best_match"]["end_ms"]), (100, 250))
        self.assertEqual(
            [(match["start_ms"], match["end_ms"]) for match in item["matches"]],
            [(100, 250), (200, 300), (0, 50)],
        )
        self.assertEqual(item["duration_ms"], 30000)
        self.assertEqual(item["web_url"], "https://drive.example/file")

    def test_adapter_search_uses_dedicated_video_read_alias(self):
        async def verify():
            adapter = VideoSearchElasticsearchIndex(
                ElasticsearchV3Config("http://elasticsearch.test", index_prefix="creative-assets", index_generation="v3")
            )
            adapter._index._request = AsyncMock(return_value={"hits": {"hits": []}})
            try:
                await adapter.search({"query": {"match_all": {}}})
                adapter._index._request.assert_awaited_once_with(
                    "POST",
                    "/creative-assets-video-v3-read/_search",
                    json_body={"query": {"match_all": {}}},
                )
            finally:
                await adapter.aclose()
        asyncio.run(verify())


if __name__ == "__main__":
    unittest.main()
