import hashlib,json
from sqlalchemy import select
from app.modules.assets.model import AssetModel
from app.modules.processing.repository import ProcessingRepository
from .model import VideoGenerationRunModel,VideoGenerationReferenceModel
MIMES={"image/jpeg","image/png","image/webp"};MAX=15*1024*1024;TOTAL=30*1024*1024
class Error(RuntimeError):
 def __init__(self,c,m,s=400):self.code,self.status_code=c,s;super().__init__(m)
def enabled(settings,tenant):
 allow={x.strip() for x in settings.VIDEO_GENERATION_CANARY_TENANT_IDS.split(",") if x.strip()}
 return bool(settings.PROCESSING_JOBS_ENABLED and settings.MANAGED_ASSET_STORAGE_ENABLED and settings.VIDEO_GENERATION_ENABLED and settings.DOLA_RENDER_GATEWAY_ENABLED and tenant in allow)
def fingerprint(tenant,user,r):return hashlib.sha256(json.dumps({"provider":"dola","model":r.model,"prompt":r.prompt.strip(),"aspect_ratio":r.aspect_ratio,"duration_seconds":r.duration_seconds,"references":r.reference_asset_ids},sort_keys=True,separators=(",",":")).encode()).hexdigest()
class Service:
 def __init__(self,session,settings):self.s,self.settings=session,settings
 def create(self,tenant,user,r):
  if not enabled(self.settings,tenant):raise Error("video_generation_disabled","Video generation is disabled.",503)
  fp=fingerprint(tenant,user,r);old=self.s.scalar(select(VideoGenerationRunModel).where(VideoGenerationRunModel.tenant_id==tenant,VideoGenerationRunModel.created_by_user_id==user,VideoGenerationRunModel.client_request_id==r.client_request_id))
  if old:
   if old.request_fingerprint!=fp:raise Error("video_generation_request_conflict","Client request conflicts.",409)
   return old
  assets=list(self.s.scalars(select(AssetModel).where(AssetModel.tenant_id==tenant,AssetModel.id.in_(r.reference_asset_ids))))
  if len(assets)!=len(r.reference_asset_ids):raise Error("reference_asset_not_found","Reference asset was not found.",404)
  total=0
  for a in assets:
   if (a.mime_type or "").split(";")[0].lower() not in MIMES:raise Error("reference_asset_unsupported","Reference image type is unsupported.")
   if a.size_bytes is not None:
    total+=a.size_bytes
    if a.size_bytes>MAX or total>TOTAL:raise Error("reference_asset_too_large","Reference image exceeds size limit.")
  run=VideoGenerationRunModel(tenant_id=tenant,provider_model=r.model,prompt=r.prompt.strip(),aspect_ratio=r.aspect_ratio,duration_seconds=r.duration_seconds,request_fingerprint=fp,client_request_id=r.client_request_id,created_by_user_id=user)
  self.s.add(run);self.s.flush()
  for i,a in enumerate(r.reference_asset_ids):self.s.add(VideoGenerationReferenceModel(tenant_id=tenant,run_id=run.id,asset_id=a,position=i))
  ProcessingRepository(self.s,self.settings).create_job(tenant_id=tenant,job_type="video_generate",entity_type="video_generation_run",entity_id=run.id,idempotency_key="video-generate:"+run.id,payload={"video_generation_run_id":run.id},max_attempts=5,provider_key="dola",provider_scope="video_generation");self.s.commit();return run
 def get(self,tenant,id):
  r=self.s.scalar(select(VideoGenerationRunModel).where(VideoGenerationRunModel.tenant_id==tenant,VideoGenerationRunModel.id==id))
  if not r:raise Error("video_generation_not_found","Video generation was not found.",404)
  return r
