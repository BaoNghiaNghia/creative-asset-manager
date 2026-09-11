from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.assets.model import AssetModel
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal,require_permission
from .model import VideoGenerationReferenceModel
from .schema import VideoGenerationRequest,VideoGenerationResponse,Capability
from .service import Service,Error,enabled,MIMES,MAX,TOTAL
router=APIRouter(prefix="/api/v1/video-generations",tags=["video-generations"]);READ=require_permission("assets.read");GENERATE=require_permission("assets.generate")
def out(r,s):
 refs=list(s.scalars(select(VideoGenerationReferenceModel.asset_id).where(VideoGenerationReferenceModel.run_id==r.id).order_by(VideoGenerationReferenceModel.position)))
 return VideoGenerationResponse(id=r.id,status=r.status,provider=r.provider,model=r.provider_model,prompt=r.prompt,aspect_ratio=r.aspect_ratio,duration_seconds=r.duration_seconds,reference_asset_ids=refs,output_asset_id=r.output_asset_id,error={"code":r.last_error_code,"message":r.last_error_message} if r.last_error_code else None,created_at=r.created_at,submitted_at=r.submitted_at,completed_at=r.completed_at)
@router.get("/capabilities")
def cap(p:CurrentPrincipal=Depends(READ)):return Capability(enabled=enabled(get_settings(),p.active_tenant_id),models=["seedance-2.0","seedance-2.5"],aspect_ratios=["16:9","9:16","1:1","4:3","3:4"],durations=[10,15,30],max_references=8,allowed_reference_mime_types=sorted(MIMES),max_reference_bytes=MAX,max_reference_total_bytes=TOTAL)
@router.post("",status_code=202)
def create(r:VideoGenerationRequest,s:Session=Depends(get_db),p:CurrentPrincipal=Depends(GENERATE)):
 try:return out(Service(s,get_settings()).create(p.active_tenant_id,p.user_id,r),s)
 except Error as e:raise HTTPException(e.status_code,detail={"code":e.code,"message":str(e)})
@router.get("/{generation_id}")
def get(generation_id:str,s:Session=Depends(get_db),p:CurrentPrincipal=Depends(READ)):
 try:return out(Service(s,get_settings()).get(p.active_tenant_id,generation_id),s)
 except Error as e:raise HTTPException(e.status_code,detail={"code":e.code,"message":str(e)})

@router.post("/{generation_id}/cancel")
def cancel(generation_id:str,s:Session=Depends(get_db),p:CurrentPrincipal=Depends(GENERATE)):
 try:return out(Service(s,get_settings()).cancel(p.active_tenant_id,generation_id,p.actor_id),s)
 except Error as e:raise HTTPException(e.status_code,detail={"code":e.code,"message":str(e)})

@router.get("/{generation_id}/video")
async def video(generation_id:str,s:Session=Depends(get_db),p:CurrentPrincipal=Depends(READ)):
 try:run=Service(s,get_settings()).get(p.active_tenant_id,generation_id)
 except Error as e:raise HTTPException(e.status_code,detail={"code":e.code,"message":str(e)})
 if run.status!="completed" or not run.output_asset_id:raise HTTPException(409,detail={"code":"video_generation_not_completed","message":"Generated video is not available yet."})
 stored=s.scalar(select(AssetStorageObjectModel).where(AssetStorageObjectModel.tenant_id==p.active_tenant_id,AssetStorageObjectModel.asset_id==run.output_asset_id,AssetStorageObjectModel.status=="stored",AssetStorageObjectModel.remote_file_id.is_not(None)))
 if stored is None:raise HTTPException(404,detail={"code":"video_generation_result_unavailable","message":"Generated video is unavailable."})
 provider=build_managed_storage_provider(get_settings())
 if isinstance(provider,UnconfiguredAssetStorageProvider):raise HTTPException(503,detail={"code":"managed_storage_unavailable","message":"Managed Storage is unavailable."})
 asset=s.get(AssetModel,run.output_asset_id)
 try:stream=await provider.open_asset(OpenStoredAssetInput(tenant_id=p.active_tenant_id,asset_id=run.output_asset_id,remote_file_id=stored.remote_file_id,content_type=asset.mime_type if asset else "video/mp4",size_bytes=asset.size_bytes if asset else None))
 except StorageProviderError as e:raise HTTPException(503 if e.retryable else 404,detail={"code":"video_generation_result_unavailable","message":"Generated video is unavailable."}) from e
 return StreamingResponse(stream.body,media_type=stream.content_type,background=BackgroundTask(stream.close),headers={"Cache-Control":"private, no-store","Content-Disposition":f'inline; filename="generated-{generation_id}.mp4"'})
