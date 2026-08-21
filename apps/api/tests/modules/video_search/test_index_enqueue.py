import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.assets.model import SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.index_enqueue import enqueue_video_search_index_job
from app.modules.video_search.model import VideoAnalysisRunModel

class IndexEnqueueTest(unittest.TestCase):
 def setUp(self):
  self.e=create_engine("sqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool); Base.metadata.create_all(self.e);self.s=Session(self.e);self.p=ProcessingRepository(self.s)
 def tearDown(self):self.s.close();self.e.dispose()
 def make_run(self,tenant,id):
  self.s.add(SourceAssetModel(id="asset"+id,tenant_id=tenant,external_source_id="src"+tenant,external_asset_id=id))
  r=VideoAnalysisRunModel(id=id,tenant_id=tenant,source_asset_id="asset"+id,source_fingerprint="f"*64,video_metadata_profile_id="p"+id,metadata_profile="p",metadata_profile_version="v1",prompt_version="p1",analysis_version="a1",ai_provider="gemini",idempotency_key=(id*64)[:64],status="completed",chunk_seconds=1,total_chunks=0,completed_chunks=0);self.s.add(r);self.s.flush();return r
 def test_dedupe_and_tenant_scope(self):
  a=self.make_run("a","a"); self.assertTrue(enqueue_video_search_index_job(tenant_id="a",run=a,processing=self.p));self.assertFalse(enqueue_video_search_index_job(tenant_id="a",run=a,processing=self.p))
  b=self.make_run("a","b"); self.assertTrue(enqueue_video_search_index_job(tenant_id="a",run=b,processing=self.p))
  c=self.make_run("b","c");self.assertTrue(enqueue_video_search_index_job(tenant_id="b",run=c,processing=self.p))
  jobs=self.s.query(ProcessingJobModel).filter_by(job_type="video_search_index").order_by(ProcessingJobModel.entity_id).all()
  self.assertEqual(len(jobs),3)
  self.assertEqual({job.entity_id for job in jobs},{a.id,b.id,c.id})
  self.assertEqual({job.payload_json["analysis_run_id"] for job in jobs},{a.id,b.id,c.id})
  self.assertEqual({job.priority for job in jobs},{10})
  self.assertEqual({job.provider_key for job in jobs},{"elasticsearch"})
  self.assertEqual({job.provider_scope for job in jobs},{"video"})
  self.assertEqual(len({job.idempotency_key for job in jobs}),3)
if __name__=="__main__":unittest.main()
