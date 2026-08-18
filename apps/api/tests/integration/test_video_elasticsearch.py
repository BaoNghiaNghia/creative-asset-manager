import os
import unittest
from uuid import uuid4

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex

URL=os.getenv("INTEGRATION_ELASTICSEARCH_URL","")

@unittest.skipUnless(URL.startswith("http"),"real Elasticsearch required")
class VideoRealElasticsearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prefix="cam-video-"+uuid4().hex[:10]
        self.video=VideoSearchElasticsearchIndex(ElasticsearchV3Config(URL,index_prefix=self.prefix,index_generation="v3"))
        self.image=ElasticsearchV3Index(ElasticsearchV3Config(URL,index_prefix=self.prefix,index_generation="v3"))
        self.physical=await self.video.create_index("000001")
        await self.video.switch_aliases(self.physical)
    async def asyncTearDown(self):
        try: await self.video._index.client.delete(f"/{self.prefix}-video-v3-*",params={"expand_wildcards":"all"})
        finally: await self.video.aclose()
    def document(self, doc_id, tenant, start=1000):
        return {"_id":doc_id,"tenant_id":tenant,"source_asset_id":"asset","source_fingerprint":"f","analysis_run_id":"run","video_metadata_profile_id":"profile","metadata_profile":"video","metadata_profile_version":"v1","prompt_version":"p1","analysis_version":"a1","ai_provider":"gemini","ai_model":"model","duration_ms":5000,"summary":"summary","source_type":"google_drive","external_source_id":"source","external_asset_id":"external","filename":"clip.mp4","mime_type":"video/mp4","web_url":None,"thumbnail_url":None,"analysis_completed_at":"2026-01-01T00:00:00+00:00","segments":[{"start_ms":start,"end_ms":start+500,"summary":"segment","visual_description":"view","speech":"","visible_text":"","keywords":["tag"],"actions":[],"objects":[],"people":[],"products":[],"locations":[],"styles":[],"colors":[],"moods":[],"confidence":0.9}]}
    async def test_real_video_index_alias_mapping_upsert_and_tenant_isolation(self):
        self.assertNotEqual(self.video.write_alias,self.image.write_alias)
        mapping=await self.video.index_mapping(self.physical)
        self.assertEqual(mapping[self.physical]["mappings"]["properties"]["segments"]["type"],"nested")
        first=self.document("tenant-a-doc","tenant-a")
        await self.video.upsert_video_document(first); await self.video.upsert_video_document(first)
        self.assertEqual(await self.video.index_count(self.physical),1)
        stored=await self.video.get_document("tenant-a-doc")
        self.assertEqual(stored["_source"]["segments"][0]["start_ms"],1000)
        self.assertEqual(len(stored["_source"]["segments"]),1)
        await self.video.upsert_video_document(self.document("tenant-b-doc","tenant-b",2000))
        self.assertEqual(await self.video.index_count(self.physical),2)
if __name__=="__main__": unittest.main()
