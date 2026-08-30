import unittest
from datetime import datetime, timezone
from app.modules.assets.model import SourceAssetModel
from app.modules.video_search.indexing import VideoIndexDataError, build_video_document, video_index_mapping
from app.modules.video_search.model import VideoAnalysisChunkModel, VideoAnalysisRunModel

class VideoIndexingTest(unittest.TestCase):
    def make_run(self):
        return VideoAnalysisRunModel(id="run",tenant_id="tenant-a",source_asset_id="asset",source_fingerprint="f"*64,video_metadata_profile_id="profile",metadata_profile="video",metadata_profile_version="v1",prompt_version="p1",analysis_version="a1",ai_provider="gemini",ai_model="model",idempotency_key="k"*64,status="completed",duration_ms=5000,chunk_seconds=30,total_chunks=2,completed_chunks=2,completed_at=datetime.now(timezone.utc))
    def source(self):
        return SourceAssetModel(id="asset",tenant_id="tenant-a",external_source_id="source",external_asset_id="external",filename="clip.mp4",mime_type="video/mp4",source_metadata={})
    def test_mapping_has_nested_segments(self):
        self.assertEqual(video_index_mapping()["properties"]["segments"]["type"],"nested")
    def test_multi_chunk_order_idempotency_and_no_provider_uri(self):
        run=self.make_run(); source=self.source()
        chunks=[VideoAnalysisChunkModel(id="b",tenant_id="tenant-a",run_id="run",chunk_index=1,source_start_ms=1,source_end_ms=2,status="completed",metadata_json={"segments":[{"start_ms":3000,"end_ms":4000,"summary":"late"}]}),VideoAnalysisChunkModel(id="a",tenant_id="tenant-a",run_id="run",chunk_index=0,source_start_ms=0,source_end_ms=1,status="completed",metadata_json={"segments":[{"start_ms":1000,"end_ms":2000,"summary":"early"}]})]
        first=build_video_document(run=run,source=source,chunks=chunks); second=build_video_document(run=run,source=source,chunks=chunks)
        self.assertEqual(first["_id"],second["_id"]); self.assertEqual([x["start_ms"] for x in first["segments"]],[1000,3000]); self.assertNotIn("provider_file_uri",first)
    def test_document_projects_design_type_from_structured_segment_keywords(self):
        run=self.make_run(); source=self.source()
        chunks=[
            VideoAnalysisChunkModel(id="a",tenant_id="tenant-a",run_id="run",chunk_index=0,source_start_ms=0,source_end_ms=1,status="completed",metadata_json={"segments":[{"start_ms":0,"end_ms":1000,"keywords":["embroidery_type:PetFull"]}]}),
            VideoAnalysisChunkModel(id="b",tenant_id="tenant-a",run_id="run",chunk_index=1,source_start_ms=1,source_end_ms=2,status="completed",metadata_json={"segments":[{"start_ms":1000,"end_ms":2000,"keywords":["embroidery_type:Other tags","embroidery_type:PetFull"]}]}),
        ]
        document=build_video_document(run=run,source=source,chunks=chunks)
        self.assertEqual(document["design_type"],["petfull","other tags"])
        self.assertEqual(video_index_mapping()["properties"]["design_type"],{"type":"keyword"})

    def test_invalid_and_incomplete_data_is_rejected(self):
        run=self.make_run(); source=self.source(); run.status="analyzing"
        with self.assertRaises(VideoIndexDataError): build_video_document(run=run,source=source,chunks=[])
        run.status="completed"; chunk=VideoAnalysisChunkModel(id="a",tenant_id="tenant-a",run_id="run",chunk_index=0,source_start_ms=0,source_end_ms=1,status="completed",metadata_json={"segments":[{"start_ms":4,"end_ms":6000}]})
        with self.assertRaises(VideoIndexDataError): build_video_document(run=run,source=source,chunks=[chunk])
