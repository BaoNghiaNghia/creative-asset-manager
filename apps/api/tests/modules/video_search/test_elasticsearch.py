import asyncio
import unittest

import httpx
from unittest.mock import AsyncMock

from app.infrastructure.search.elasticsearch_v2 import (
    ElasticsearchV3Config,
    ElasticsearchV3Index,
    ElasticsearchV3RequestError,
)
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex

class VideoElasticsearchTest(unittest.TestCase):
    def setUp(self):
        self.config=ElasticsearchV3Config("http://elasticsearch.test",index_prefix="creative-assets",index_generation="v3")
        self.video=VideoSearchElasticsearchIndex(self.config)
        self.image=ElasticsearchV3Index(self.config)
    def test_alias_and_physical_index_are_separate_from_image(self):
        self.assertNotEqual(self.video.write_alias,self.image.write_alias)
        self.assertNotEqual(self.video.physical_index_name("v1"),self.image.physical_index_name("v1"))
    def test_video_mapping_is_nested_and_image_mapping_unchanged(self):
        self.assertEqual(self.video.index_definition()["mappings"]["properties"]["segments"]["type"],"nested")
        self.assertNotIn("segments",ElasticsearchV3Index.index_definition()["mappings"]["properties"])
    def test_document_upsert_requires_deterministic_id(self):
        import asyncio
        with self.assertRaises(ValueError): asyncio.run(self.video.upsert_video_document({}))

    def test_ensure_index_creates_only_when_settings_are_absent(self):
        async def verify():
            self.video._index._request=AsyncMock(return_value={})
            await self.video.ensure_index("v1")
            self.assertEqual(self.video._index._request.call_count,2)
            self.video._index._request.reset_mock()
            self.video._index._request=AsyncMock(return_value={"index": {}})
            await self.video.ensure_index("v1")
            self.video._index._request.assert_awaited_once_with(
                "GET", "/creative-assets-video-v3-v1/_settings",
                allow_not_found=True,
            )
        asyncio.run(verify())

    def test_transport_preserves_deterministic_http_status(self):
        async def request():
            client=httpx.AsyncClient(
                base_url="http://elasticsearch.test",
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(400, request=request)
                ),
            )
            index=ElasticsearchV3Index(self.config,client=client)
            try:
                with self.assertRaises(ElasticsearchV3RequestError) as caught:
                    await index._request("PUT","/invalid")
                self.assertEqual(caught.exception.status_code,400)
            finally:
                await index.aclose()
        asyncio.run(request())
if __name__=="__main__": unittest.main()
