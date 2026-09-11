import asyncio,io,json
from pathlib import Path
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cam_runtime.app import create_app
from cam_runtime.config import load_settings
from cam_runtime.idempotency import GenerationRepository
from cam_runtime.service import GatewayService
from cam_runtime.contracts import parse_metadata,fingerprint
def env(tmp):
 return {"DOLA_INTERNAL_API_KEY":"local-internal-key","DOLA_STATE_ROOT":str(tmp/"state"),"DOLA_RUNTIME_ROOT":str(tmp/"run")}
class Fake:
 def __init__(self):self.calls=0;self.rows={}
 async def submit(self,gid,req,refs):
  self.calls+=1;tid="task-"+gid;self.rows[tid]={"status":"queued","video_url":None};return tid
 async def status(self,tid):return self.rows[tid]
def client(tmp):
 settings=load_settings(env(tmp),source_root=tmp/"source");app=create_app(settings,upstream_app=FastAPI());fake=Fake();app.state.gateway=GatewayService(GenerationRepository(settings.paths.db_dir/"gateway.db"),fake,settings.paths);return TestClient(app),fake,settings
def png(color="red"):
 out=io.BytesIO();Image.new("RGB",(2,2),color).save(out,"PNG");return out.getvalue()
def data(prompt="hello",ratio="1:1",duration=10,model="seedance-2.0",refs=()):
 return [("metadata",(None,json.dumps({"prompt":prompt,"model":model,"aspect_ratio":ratio,"duration_seconds":duration})))] + [("references",("x.png",v,"image/png")) for v in refs]
def auth(key="k"):return {"Authorization":"Bearer local-internal-key","Idempotency-Key":key}
def test_auth_and_request_validation(tmp_path):
 c,_,_=client(tmp_path);body=data()
 assert c.post("/internal/v1/video-generations",files=body).status_code==401
 assert c.get("/internal/v1/video-generations/nope").status_code==401
 assert c.get("/internal/v1/video-generations/nope/content").status_code==401
 assert c.post("/internal/v1/video-generations",headers={"Authorization":"Bearer local-internal-key"},files=body).json()["error"]["code"]=="missing_idempotency_key"
 assert c.post("/internal/v1/video-generations",headers=auth(" "*201),files=body).json()["error"]["code"]=="missing_idempotency_key"
 bad=[("metadata",(None,"{}"))];assert c.post("/internal/v1/video-generations",headers=auth(),files=bad).json()["error"]["code"]=="invalid_request"
 bad=data(refs=(b"not-image",));assert c.post("/internal/v1/video-generations",headers=auth(),files=bad).json()["error"]["code"]=="invalid_request"
def test_fingerprint_contract():
 a=parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.0","aspect_ratio":"1:1","duration_seconds":10}),[("image/png",png("red")),("image/png",png("blue"))])
 assert fingerprint(a)==fingerprint(a)
 for changed in [parse_metadata(json.dumps({"prompt":"y","model":"seedance-2.0","aspect_ratio":"1:1","duration_seconds":10}),[("image/png",png("red")),("image/png",png("blue"))]),parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.5","aspect_ratio":"1:1","duration_seconds":10}),[("image/png",png("red")),("image/png",png("blue"))]),parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.0","aspect_ratio":"16:9","duration_seconds":10}),[("image/png",png("red")),("image/png",png("blue"))]),parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.0","aspect_ratio":"1:1","duration_seconds":15}),[("image/png",png("red")),("image/png",png("blue"))]),parse_metadata(json.dumps({"prompt":"x","model":"seedance-2.0","aspect_ratio":"1:1","duration_seconds":10}),[("image/png",png("blue")),("image/png",png("red"))])]:assert fingerprint(a)!=fingerprint(changed)
def test_idempotency_restart_and_status_content(tmp_path):
 c,f,s=client(tmp_path);r=c.post("/internal/v1/video-generations",headers=auth("same"),files=data(refs=(png(),))).json();again=c.post("/internal/v1/video-generations",headers=auth("same"),files=data(refs=(png(),))).json()
 assert f.calls==1 and r["generation_id"]==again["generation_id"] and again["idempotent_replay"]
 assert c.post("/internal/v1/video-generations",headers=auth("same"),files=data(prompt="changed")).status_code==409
 gid=r["generation_id"];assert c.get("/internal/v1/video-generations/"+gid,headers={"Authorization":"Bearer local-internal-key"}).json()["status"]=="submitted"
 tid=next(iter(f.rows));f.rows[tid]={"status":"completed","video_url":"/videos/result.mp4"};(s.paths.downloads_dir/"result.mp4").write_bytes(b"video")
 assert c.get("/internal/v1/video-generations/"+gid,headers={"Authorization":"Bearer local-internal-key"}).json()["content_available"]
 got=c.get("/internal/v1/video-generations/"+gid+"/content",headers={"Authorization":"Bearer local-internal-key"});assert got.status_code==200 and got.content==b"video" and str(s.paths.downloads_dir) not in got.text
 assert GenerationRepository(s.paths.db_dir/"gateway.db").get(gid)["generation_id"]==gid
def test_content_never_uses_client_path(tmp_path):
 c,f,s=client(tmp_path);r=c.post("/internal/v1/video-generations",headers=auth(),files=data()).json();tid=next(iter(f.rows));f.rows[tid]={"status":"completed","video_url":"/videos/../../etc/passwd"}
 assert c.get("/internal/v1/video-generations/"+r["generation_id"]+"/content",headers={"Authorization":"Bearer local-internal-key"}).json()["error"]["code"] in {"generation_content_missing","generation_content_invalid"}
def test_concurrent_repository_insert(tmp_path):
 repo=GenerationRepository(tmp_path/"gateway.db")
 async def one():return await asyncio.to_thread(repo.create_or_get,"concurrent","f")
 async def both(): return await asyncio.gather(one(),one())
 rows=asyncio.run(both());assert len({x[0]["generation_id"] for x in rows})==1
