from types import SimpleNamespace
from app.modules.visual_search.job_coverage import VisualJobCoverageReader
from app.modules.visual_search.lifecycle import visual_index_job_key
class S:
 def __init__(self,rows):self.rows=rows
 def scalars(self,*x):return SimpleNamespace(all=lambda:self.rows)
def job(a,h,status):return SimpleNamespace(entity_id=a,idempotency_key=visual_index_job_key(a,h),status=status,job_type="visual_index_sync")
def test_current_job_states_and_failed_current_projection():
 h="a"*64; rows=[job("a",h,"pending"),job("b",h,"retry"),job("c",h,"processing"),job("d",h,"failed"),job("e","b"*64,"failed")]
 out=VisualJobCoverageReader(S(rows)).counts("t",{x:h for x in "abcde"},{"d"})
 assert out=={"visual_jobs_pending":2,"visual_jobs_processing":1,"visual_jobs_failed":0}
