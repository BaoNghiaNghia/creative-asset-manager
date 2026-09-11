import asyncio,json
from pathlib import Path
from cam_runtime.contracts import parse_metadata,fingerprint
from cam_runtime.idempotency import GenerationRepository
from cam_runtime.service import GatewayService
from cam_runtime.errors import GatewayError
class Paths:
 def __init__(self,root):self.tmp_dir=root/"run";self.downloads_dir=root/"downloads";self.tmp_dir.mkdir(parents=True);self.downloads_dir.mkdir(parents=True)
class Adapter:
 def __init__(self,mode="ok"):self.calls=0;self.mode=mode
 async def submit(self,*args):
  self.calls+=1
  if self.mode=="raise":raise RuntimeError("ambiguous")
  return "video_task"
 async def status(self,*args):return {"status":"queued","video_url":None}
def request():
 return parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.0","aspect_ratio":"1:1","duration_seconds":10}),[])
def test_restart_unknown_replay_never_resubmits(tmp_path):
 repo=GenerationRepository(tmp_path/"gateway.db");row,_=repo.create_or_get("k","f");repo.mark_submission_attempt(row["generation_id"])
 adapter=Adapter();svc=GatewayService(GenerationRepository(tmp_path/"gateway.db"),adapter,Paths(tmp_path))
 row,replay=asyncio.run(svc.submit("k","f",request()))
 assert replay and row["state"]=="submission_unknown" and adapter.calls==0 and row["submission_attempted_at"]
 try:asyncio.run(svc.submit("k","different",request()))
 except GatewayError as e:assert e.code=="idempotency_key_conflict"
 else:assert False
def test_ambiguous_adapter_exception_is_unknown(tmp_path):
 repo=GenerationRepository(tmp_path/"gateway.db");svc=GatewayService(repo,Adapter("raise"),Paths(tmp_path))
 try:asyncio.run(svc.submit("k","f",request()))
 except GatewayError as e:assert e.code=="upstream_submission_state_unknown"
 else:assert False
 row=repo.create_or_get("k","f")[0];assert row["state"]=="submission_unknown" and row["submission_attempted_at"]
def test_post_accept_persist_failure_is_unknown(tmp_path):
 class FailingRepo(GenerationRepository):
  def update(self,gid,**fields):
   if fields.get("upstream_task_id"):raise RuntimeError("crash before persist")
   return super().update(gid,**fields)
 repo=FailingRepo(tmp_path/"gateway.db");adapter=Adapter();svc=GatewayService(repo,adapter,Paths(tmp_path))
 try:asyncio.run(svc.submit("k","f",request()))
 except GatewayError as e:assert e.code=="upstream_submission_state_unknown"
 else:assert False
 fresh=GatewayService(GenerationRepository(tmp_path/"gateway.db"),adapter,Paths(tmp_path/"fresh"))
 row,replay=asyncio.run(fresh.submit("k","f",request()))
 assert replay and row["state"]=="submission_unknown" and adapter.calls==1

def test_unknown_status_and_content_are_side_effect_free(tmp_path):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from cam_runtime.app import create_app
 from cam_runtime.config import load_settings
 settings=load_settings({"DOLA_INTERNAL_API_KEY":"local-internal-key","DOLA_STATE_ROOT":str(tmp_path/"state"),"DOLA_RUNTIME_ROOT":str(tmp_path/"run")},source_root=tmp_path/"source")
 app=create_app(settings,upstream_app=FastAPI()); repo=GenerationRepository(settings.paths.db_dir/"gateway.db"); fake=Adapter(); app.state.gateway=GatewayService(repo,fake,settings.paths)
 row,_=repo.create_or_get("unknown","f"); repo.mark_submission_attempt(row["generation_id"]); c=TestClient(app); h={"Authorization":"Bearer local-internal-key"}
 response=c.get("/internal/v1/video-generations/"+row["generation_id"],headers=h).json(); assert response["status"]=="submission_unknown" and response["error_code"]=="upstream_submission_state_unknown"
 assert c.get("/internal/v1/video-generations/"+row["generation_id"]+"/content",headers=h).status_code==409 and fake.calls==0
