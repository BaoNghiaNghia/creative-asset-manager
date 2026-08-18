import logging
import unittest
from threading import Event
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, WorkerDependencies
from app.modules.assets.model import SourceAssetModel
from app.modules.video_search.index_handler import VideoSearchIndexJobHandler
from app.modules.video_search.model import VideoAnalysisChunkModel, VideoAnalysisRunModel

class IndexHandlerTest(unittest.TestCase):
 def setUp(self):
  self.e=create_engine("sqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool); Base.metadata.create_all(self.e)
  self.s=Session(self.e); self.settings=Settings(ELASTICSEARCH_URL="http://test",ELASTICSEARCH_V2_ENABLED=True)
 def tearDown(self): self.s.close(); self.e.dispose()
 def context(self,tenant="a",run="run",cancel=False):
  return JobHandlerContext(ClaimedJob("job",tenant,"video_search_index","video_analysis_run",run,{"analysis_run_id":run},1,"w"),WorkerDependencies(session_factory=lambda:Session(self.e)),Event(),Event(),logging.LoggerAdapter(logging.getLogger("t"),{}))
 def data(self,tenant="a",status="completed",chunks=True,valid=True):
  source=SourceAssetModel(id="asset",tenant_id=tenant,external_source_id="source",external_asset_id="external",filename="x.mp4",mime_type="video/mp4",source_metadata={}); self.s.add(source)
  run=VideoAnalysisRunModel(id="run",tenant_id=tenant,source_asset_id="asset",source_fingerprint="f"*64,video_metadata_profile_id="profile",metadata_profile="p",metadata_profile_version="v1",prompt_version="p1",analysis_version="a1",ai_provider="gemini",ai_model="m",idempotency_key="k"*64,status=status,duration_ms=1000,chunk_seconds=1,total_chunks=1,completed_chunks=1 if chunks else 0)
  self.s.add(run)
  if chunks:self.s.add(VideoAnalysisChunkModel(id="chunk",tenant_id=tenant,run_id="run",chunk_index=0,source_start_ms=0,source_end_ms=1,status="completed",metadata_json={"segments":[{"start_ms":0,"end_ms":500}] if valid else [{"start_ms":0,"end_ms":2000}]}))
  self.s.commit()
 def test_success_and_repeat(self):
  self.data()
  with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
   instance=cls.return_value; instance.upsert_video_document=AsyncMock(); instance.aclose=AsyncMock(); result=self.context()
   self.assertEqual(VideoSearchIndexJobHandler(self.settings)(result).outcome.value,"completed")
   self.assertEqual(VideoSearchIndexJobHandler(self.settings)(result).outcome.value,"completed")
   self.assertEqual(instance.upsert_video_document.call_count,2)
   self.assertEqual(instance.aclose.await_count,2)
 def test_missing_tenant_incomplete_and_malformed_are_nonretryable(self):
  self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"non_retryable_failure")
  self.data(tenant="b"); self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"non_retryable_failure")
  self.s.rollback(); self.e.dispose()
 def test_malformed_is_nonretryable(self):
  self.data(valid=False)
  self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"non_retryable_failure")
 def test_incomplete_unfinished_and_cancellation_have_no_upsert(self):
  self.data(status="pending")
  with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
      instance=cls.return_value; instance.upsert_video_document=AsyncMock()
      self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"non_retryable_failure")
      instance.upsert_video_document.assert_not_called()
  self.s.close(); self.e.dispose(); self.setUp(); self.data(chunks=False)
  with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
      instance=cls.return_value; instance.upsert_video_document=AsyncMock()
      self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"non_retryable_failure")
      instance.upsert_video_document.assert_not_called()
  self.s.close(); self.e.dispose(); self.setUp(); self.data()
  context=self.context(); context.cancellation_requested.set()
  with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
      self.assertEqual(VideoSearchIndexJobHandler(self.settings)(context).outcome.value,"cancelled")
      cls.assert_not_called()

 def test_transport_errors_are_retryable(self):
  from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
  self.data()
  for message in ("connection failed","timeout","returned 429","returned 502","returned 503","returned 504"):
      with self.subTest(message=message), patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
          instance=cls.return_value; instance.upsert_video_document=AsyncMock(side_effect=ElasticsearchV3RequestError(message)); instance.aclose=AsyncMock()
          self.assertEqual(VideoSearchIndexJobHandler(self.settings)(self.context()).outcome.value,"retryable_failure")
          instance.aclose.assert_awaited_once()

 def test_deterministic_request_rejection_is_nonretryable(self):
  from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
  self.data()
  with patch("app.modules.video_search.index_handler.VideoSearchElasticsearchIndex") as cls:
      instance=cls.return_value
      instance.upsert_video_document=AsyncMock(side_effect=ElasticsearchV3RequestError("mapping rejected",status_code=400)); instance.aclose=AsyncMock()
      result=VideoSearchIndexJobHandler(self.settings)(self.context())
  self.assertEqual(result.outcome.value,"non_retryable_failure")
  instance.aclose.assert_awaited_once()
  self.assertEqual(result.error_code,"video_index_elasticsearch_request_rejected")

if __name__=="__main__":unittest.main()
