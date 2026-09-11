import asyncio,uuid
from pathlib import Path
from urllib.parse import urlparse
from .errors import GatewayError
class GatewayService:
 def __init__(self,repo,adapter,paths):self.repo,self.adapter,self.paths=repo,adapter,paths
 async def submit(self,key,fp,req):
  row,replay=self.repo.create_or_get(key,fp)
  if row["request_fingerprint"]!=fp:raise GatewayError("idempotency_key_conflict","Idempotency-Key was already used for a different request",409)
  if replay:return row,True
  d=self.paths.tmp_dir/"references"/row["generation_id"];d.mkdir(parents=True,exist_ok=True);refs=[]
  for i,(blob,suffix) in enumerate(zip(req.references,req.suffixes)):p=d/("reference_"+str(i)+suffix);p.write_bytes(blob);refs.append(str(p))
  try:task=await self.adapter.submit(row["generation_id"],req,refs)
  except Exception:self.repo.update(row["generation_id"],state="failed",last_error_code="upstream_submission_failed",last_error_message="Upstream submission failed");raise GatewayError("upstream_submission_failed","Upstream submission failed",502)
  self.repo.update(row["generation_id"],upstream_task_id=task,state="submitted");return self.repo.get(row["generation_id"]),False
 async def status(self,gid):
  row=self.repo.get(gid)
  if not row:raise GatewayError("generation_not_found","Generation was not found",404)
  if row["state"] in {"completed","failed"} or not row["upstream_task_id"]:return row
  try:u=await self.adapter.status(row["upstream_task_id"])
  except Exception:raise GatewayError("upstream_status_failed","Upstream status lookup failed",502)
  state={"queued":"submitted","processing":"running","completed":"completed","failed":"failed"}.get(u.get("status"),"running");f={"state":state}
  if state=="failed":f.update(last_error_code="upstream_status_failed",last_error_message="Upstream generation failed")
  if state=="completed":
   n=Path(urlparse(str(u.get("video_url") or "")).path).name
   if n:f["output_name"]=n
  self.repo.update(gid,**f);return self.repo.get(gid)
 def content_path(self,row):
  if row["state"]!="completed":raise GatewayError("generation_not_complete","Generation is not complete",409)
  if not row.get("output_name"):raise GatewayError("generation_content_missing","Generated content is unavailable",404)
  root=self.paths.downloads_dir.resolve();p=(root/row["output_name"]).resolve()
  if root not in p.parents or not p.is_file() or p.suffix.lower() not in {".mp4",".webm"}:raise GatewayError("generation_content_invalid","Generated content is invalid",404)
  return p
class VendoredAdapter:
 async def submit(self,gid,req,refs):
  import server
  task="video_"+uuid.uuid4().hex;server.store.create(task,req.model,req.prompt,req.aspect_ratio,req.duration_seconds,reference_images="[]")
  async def run():
   try:server.store.update(task,status="processing");r=await server.pool.generate_video(req.prompt,req.aspect_ratio,req.duration_seconds,req.model,reference_image_paths=refs);server.store.update(task,status="completed",video_url="/videos/"+Path(r["local_path"]).name)
   except Exception as e:server.store.update(task,status="failed",error=str(e)[:500])
  asyncio.create_task(run());return task
 async def status(self,task):
  import server
  r=server.store.get(task)
  if not r:raise RuntimeError("upstream task missing")
  return r
