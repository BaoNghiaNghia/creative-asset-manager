from __future__ import annotations
from datetime import timedelta
from secrets import token_urlsafe
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.assets.content_resolver import SourceAssetContentResolver
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.public_review.authorization import PublicShareAccessDenied, PublicShareScopeService
from app.modules.public_review.model import PublicShareModel
from app.modules.public_review.rate_limit import PublicRateLimitExceeded, consume
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.service import PublicReviewService, utcnow
router=APIRouter(prefix="/api/public/review",tags=["public-review"])
COOKIE="cam_public_review_session"; TTL=timedelta(hours=12)
def denied(): return HTTPException(404,detail={"code":"public_review_unavailable"})
def limited(): return HTTPException(429,detail={"code":"public_review_unavailable"})
def safe(payload,status=200):
 r=JSONResponse(jsonable_encoder(payload),status_code=status); r.headers.update({"Cache-Control":"no-store, private","Pragma":"no-cache","Referrer-Policy":"no-referrer","Vary":"Cookie","X-Content-Type-Options":"nosniff"}); return r
def client_key(request): return request.client.host if request.client else "unknown"
def limit(session,request,operation,maximum):
 try: consume(session,operation=operation,client_identity=client_key(request),limit=maximum)
 except PublicRateLimitExceeded: raise limited()
def user(request,public_id):
 token=request.cookies.get(COOKIE)
 if not token: raise denied()
 with SessionLocal() as s:
  try: return PublicShareScopeService(s).resolve_principal(raw_session_token=token,expected_public_id=public_id)
  except (PublicShareAccessDenied,ValueError): raise denied()
def permitted(scope,p,asset,source):
 try: scope.authorize_asset_source_pair(principal=p,asset_id=asset,source_asset_id=source); return True
 except PublicShareAccessDenied: return False
def doc(asset,source,pid):
 base=f"/api/public/review/{pid}/assets/{asset.id}"
 return {"asset_id":asset.id,"source_asset_id":source.id,"filename":source.filename or "Untitled asset","media_type":source.mime_type or asset.mime_type,"folder_context":list((source.source_metadata or {}).get("parents") or [])[:8],"thumbnail_url":f"{base}/thumbnail?source_asset_id={source.id}","preview_url":f"{base}/preview?source_asset_id={source.id}"}
def asset_pair(p,asset_id,source_id):
 with SessionLocal() as s:
  scope=PublicShareScopeService(s); q=select(AssetModel,SourceAssetModel).join(AssetSourceLinkModel,(AssetSourceLinkModel.tenant_id==AssetModel.tenant_id)&(AssetSourceLinkModel.asset_id==AssetModel.id)).join(SourceAssetModel,(SourceAssetModel.tenant_id==AssetSourceLinkModel.tenant_id)&(SourceAssetModel.id==AssetSourceLinkModel.source_asset_id)).where(AssetModel.tenant_id==p.tenant_id,AssetModel.id==asset_id,SourceAssetModel.deleted_at.is_(None))
  if source_id: q=q.where(SourceAssetModel.id==source_id)
  rows=[x for x in s.execute(q).all() if permitted(scope,p,x[0].id,x[1].id)]
  if len(rows)!=1: raise denied()
  a,src=rows[0]; s.expunge(a);s.expunge(src);return a,src
@router.post("/{public_share_id}/session",status_code=201)
def session(public_share_id:str,request:Request,body:dict):
 parsed=urlsplit(get_settings().PUBLIC_APP_URL); origin=request.headers.get("origin")
 if not origin or origin.rstrip("/") != f"{parsed.scheme}://{parsed.netloc}".rstrip("/"): raise denied()
 secret=body.get("secret") if isinstance(body,dict) else None
 if not isinstance(secret,str) or not secret or len(secret)>512: raise denied()
 with SessionLocal() as s:
  limit(s,request,"session",20); service=PublicReviewService(PublicReviewRepository(s))
  try:
   share=service.verify_share_secret(public_share_id,secret); expiry=min(utcnow()+TTL,share.expires_at) if share.expires_at else utcnow()+TTL; token=token_urlsafe(32); service.create_session(tenant_id=share.tenant_id,share_id=share.id,raw_session_token=token,expires_at=expiry);s.commit()
  except (LookupError,ValueError): s.rollback();raise denied()
 r=safe({"public_id":public_share_id,"expires_at":expiry},201);r.set_cookie(COOKIE,token,httponly=True,secure=get_settings().is_production,samesite="lax",path=f"/api/public/review/{public_share_id}",max_age=max(1,int((expiry-utcnow()).total_seconds())));return r
@router.get("/{public_share_id}/bootstrap")
def bootstrap(public_share_id:str,request:Request):
 p=user(request,public_share_id)
 with SessionLocal() as s:
  share=s.scalar(select(PublicShareModel).where(PublicShareModel.tenant_id==p.tenant_id,PublicShareModel.id==p.share_id))
  if not share: raise denied()
  return safe({"public_id":p.public_id,"name":share.name,"allow_comments":p.allow_comments,"allow_download":p.allow_download,"expires_at":p.expires_at})
@router.get("/{public_share_id}/folders")
def folders(public_share_id:str,request:Request):
 p=user(request,public_share_id)
 with SessionLocal() as s:
  scope=PublicShareScopeService(s); access=scope.scoped_accesses(principal=p); rows=s.execute(select(SourceAssetModel).where(SourceAssetModel.tenant_id==p.tenant_id,SourceAssetModel.external_source_id.in_(list(access)),SourceAssetModel.deleted_at.is_(None))).scalars()
  return safe({"items":[{"source_id":x.external_source_id,"folder_id":x.external_asset_id,"name":x.filename or "Untitled folder"} for x in rows if x.external_asset_id in access[x.external_source_id].folder_ids]})
@router.get("/{public_share_id}/folders/{folder_id}/children")
def children(public_share_id:str,folder_id:str,request:Request,source_id:str=Query(...,max_length=36),limit_value:int=Query(50,ge=1,le=100)):
 p=user(request,public_share_id)
 with SessionLocal() as s:
  limit(s,request,"traversal",120);scope=PublicShareScopeService(s)
  if not scope.allows_external_asset(principal=p,external_source_id=source_id,external_asset_id=folder_id): raise denied()
  rows=s.execute(select(SourceAssetModel).where(SourceAssetModel.tenant_id==p.tenant_id,SourceAssetModel.external_source_id==source_id,SourceAssetModel.deleted_at.is_(None))).scalars();out=[]
  for x in rows:
   if folder_id in ((x.source_metadata or {}).get("parents") or []) and scope.allows_external_asset(principal=p,external_source_id=source_id,external_asset_id=x.external_asset_id): out.append({"id":x.external_asset_id,"name":x.filename or "Untitled","mime_type":x.mime_type})
   if len(out)>=limit_value: break
  s.commit();return safe({"items":out})
@router.get("/{public_share_id}/assets/{asset_id}")
def metadata(public_share_id:str,asset_id:str,request:Request,source_asset_id:str|None=None):
 a,src=asset_pair(user(request,public_share_id),asset_id,source_asset_id);return safe(doc(a,src,public_share_id))
@router.get("/{public_share_id}/search")
def search(public_share_id:str,request:Request,q:str=Query(...,min_length=1,max_length=200),limit_value:int=Query(25,ge=1,le=100)):
 p=user(request,public_share_id); needle=" ".join(q.split()).casefold()
 if not needle: raise HTTPException(422,detail={"code":"invalid_search_query"})
 with SessionLocal() as s:
  limit(s,request,"search",60);scope=PublicShareScopeService(s);rows=s.execute(select(AssetModel,SourceAssetModel).join(AssetSourceLinkModel,(AssetSourceLinkModel.tenant_id==AssetModel.tenant_id)&(AssetSourceLinkModel.asset_id==AssetModel.id)).join(SourceAssetModel,(SourceAssetModel.tenant_id==AssetSourceLinkModel.tenant_id)&(SourceAssetModel.id==AssetSourceLinkModel.source_asset_id)).where(AssetModel.tenant_id==p.tenant_id,SourceAssetModel.deleted_at.is_(None)).limit(2000)).all();out=[]
  for a,src in rows:
   if needle in (src.filename or "").casefold() and permitted(scope,p,a.id,src.id): out.append(doc(a,src,public_share_id))
   if len(out)>=limit_value: break
  s.commit();return safe({"items":out,"query":q})
async def media(public_share_id,asset_id,request,source_id):
 p=user(request,public_share_id);a,src=asset_pair(p,asset_id,source_id);resolver=SourceAssetContentResolver(SessionLocal)
 async def body():
  async with resolver.open(tenant_id=p.tenant_id,source_asset_id=src.id,range_header=request.headers.get("range")) as stream:
   async for chunk in stream.body: yield chunk
 r=StreamingResponse(body(),media_type=src.mime_type or "application/octet-stream");r.headers.update({"Cache-Control":"no-store, private","Pragma":"no-cache","Referrer-Policy":"no-referrer","Vary":"Cookie","X-Content-Type-Options":"nosniff"});return r
@router.get("/{public_share_id}/assets/{asset_id}/thumbnail")
async def thumbnail(public_share_id:str,asset_id:str,request:Request,source_asset_id:str|None=None): return await media(public_share_id,asset_id,request,source_asset_id)
@router.get("/{public_share_id}/assets/{asset_id}/preview")
async def preview(public_share_id:str,asset_id:str,request:Request,source_asset_id:str|None=None): return await media(public_share_id,asset_id,request,source_asset_id)
from app.modules.public_review.model import AssetAnnotationModel, PublicShareSessionModel
from app.modules.public_review.schema import validate_annotation_document
from app.modules.public_review.service import validate_anchors

def origin_required(request):
 parsed=urlsplit(get_settings().PUBLIC_APP_URL); origin=request.headers.get("origin")
 if not origin or origin.rstrip("/") != f"{parsed.scheme}://{parsed.netloc}".rstrip("/"): raise denied()
def annotation_dto(row,p,display_name=None):
 return {"id":row.id,"author":{"display_name": display_name or "Guest"},"content_json":row.content_json,"plain_text":row.plain_text,"parent_annotation_id":row.parent_annotation_id,"anchor_x":row.anchor_x,"anchor_y":row.anchor_y,"created_at":row.created_at,"updated_at":row.updated_at,"can_edit":row.guest_id==p.guest_id,"can_delete":row.guest_id==p.guest_id}
def ensure_guest(s,p):
 row=s.scalar(select(PublicShareSessionModel).where(PublicShareSessionModel.tenant_id==p.tenant_id,PublicShareSessionModel.share_id==p.share_id,PublicShareSessionModel.id==p.session_id).with_for_update())
 if row is None or row.revoked_at is not None: raise denied()
 if row.guest_id: return row.guest_id
 name="Guest "+token_urlsafe(4).replace("-","").replace("_","")[:4].upper()
 guest=PublicReviewRepository(s).create_guest(tenant_id=p.tenant_id,share_id=p.share_id,display_name=name);row.guest_id=guest.id;s.flush();return guest.id
@router.get("/{public_share_id}/assets/{asset_id}/annotations")
def annotations(public_share_id:str,asset_id:str,request:Request,source_asset_id:str|None=None):
 p=user(request,public_share_id);_a,src=asset_pair(p,asset_id,source_asset_id)
 with SessionLocal() as s:
  rows=PublicReviewRepository(s).list_annotations_with_guests(p.tenant_id,p.share_id,asset_id,src.id);return safe({"items":[annotation_dto(x,p,name) for x,name in rows]})
@router.post("/{public_share_id}/assets/{asset_id}/annotations",status_code=201)
def create_annotation(public_share_id:str,asset_id:str,request:Request,body:dict,source_asset_id:str|None=None):
 origin_required(request);p=user(request,public_share_id);_a,src=asset_pair(p,asset_id,source_asset_id)
 with SessionLocal() as s:
  limit(s,request,"annotation_write",30)
  if not p.allow_comments: raise denied()
  try:
   content=validate_annotation_document(body.get("content_json"));validate_anchors(body.get("anchor_x"),body.get("anchor_y"));guest=ensure_guest(s,p)
   row=PublicReviewRepository(s).create_annotation(tenant_id=p.tenant_id,share_id=p.share_id,asset_id=asset_id,source_asset_id=src.id,guest_id=guest,parent_annotation_id=body.get("parent_annotation_id"),anchor_x=body.get("anchor_x"),anchor_y=body.get("anchor_y"),content_json=content,status="open")
   s.commit(); payload=annotation_dto(row,p,PublicReviewRepository(s).get_guest(p.tenant_id,p.share_id,row.guest_id).display_name); payload["can_edit"]=True; payload["can_delete"]=True; return safe(payload,201)
  except (ValueError,LookupError): s.rollback();raise denied()
@router.patch("/{public_share_id}/annotations/{annotation_id}")
def update_annotation(public_share_id:str,annotation_id:str,request:Request,body:dict):
 origin_required(request);p=user(request,public_share_id)
 with SessionLocal() as s:
  limit(s,request,"annotation_write",30)
  if not p.allow_comments: raise denied()
  row=s.scalar(select(AssetAnnotationModel).where(AssetAnnotationModel.tenant_id==p.tenant_id,AssetAnnotationModel.share_id==p.share_id,AssetAnnotationModel.id==annotation_id))
  if row is None or row.guest_id!=p.guest_id: raise denied()
  try:
   values={"content_json":validate_annotation_document(body["content_json"])} if "content_json" in body else {}
   if "anchor_x" in body or "anchor_y" in body: validate_anchors(body.get("anchor_x"),body.get("anchor_y"));values.update(anchor_x=body.get("anchor_x"),anchor_y=body.get("anchor_y"))
   row=PublicReviewRepository(s).update_annotation(p.tenant_id,p.share_id,annotation_id,**values);s.commit();return safe(annotation_dto(row,p,PublicReviewRepository(s).get_guest(p.tenant_id,p.share_id,row.guest_id).display_name))
  except (ValueError,KeyError):s.rollback();raise denied()
@router.delete("/{public_share_id}/annotations/{annotation_id}")
def delete_annotation(public_share_id:str,annotation_id:str,request:Request):
 origin_required(request);p=user(request,public_share_id)
 with SessionLocal() as s:
  limit(s,request,"annotation_write",30)
  if not p.allow_comments: raise denied()
  row=s.scalar(select(AssetAnnotationModel).where(AssetAnnotationModel.tenant_id==p.tenant_id,AssetAnnotationModel.share_id==p.share_id,AssetAnnotationModel.id==annotation_id))
  if row is None or row.guest_id!=p.guest_id:raise denied()
  PublicReviewRepository(s).delete_annotation(p.tenant_id,p.share_id,annotation_id);s.commit();return safe({"deleted":True})
