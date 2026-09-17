from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.public_review.model import AssetAnnotationModel, PublicShareGuestModel, PublicShareModel
from app.modules.assets.model import SourceAssetModel
router=APIRouter(prefix="/api/v1/public-review/board",tags=["review-board"])
READ=require_permission("public_review.read")
def rowdoc(r,g,s,src,count=0): return {"id":r.id,"status":r.status,"annotation_preview":r.plain_text[:500],"content_json":r.content_json,"created_at":r.created_at,"updated_at":r.updated_at,"anchor_x":r.anchor_x,"anchor_y":r.anchor_y,"reviewer":{"display_name":g.display_name},"share":{"id":s.id,"name":s.name},"asset":{"asset_id":r.asset_id,"source_asset_id":r.source_asset_id,"filename":src.filename,"media_type":src.mime_type},"reply_count":count,"resolved_at":r.resolved_at,"resolver":r.resolved_by}
def base(tenant): return select(AssetAnnotationModel,PublicShareGuestModel,PublicShareModel,SourceAssetModel).join(PublicShareGuestModel,(PublicShareGuestModel.tenant_id==AssetAnnotationModel.tenant_id)&(PublicShareGuestModel.id==AssetAnnotationModel.guest_id)).join(PublicShareModel,(PublicShareModel.tenant_id==AssetAnnotationModel.tenant_id)&(PublicShareModel.id==AssetAnnotationModel.share_id)).join(SourceAssetModel,(SourceAssetModel.tenant_id==AssetAnnotationModel.tenant_id)&(SourceAssetModel.id==AssetAnnotationModel.source_asset_id)).where(AssetAnnotationModel.tenant_id==tenant,AssetAnnotationModel.parent_annotation_id.is_(None))
@router.get("/issues")
def issues(status:str=Query("open",pattern="^(open|resolved|all)$"),page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),principal:CurrentPrincipal=Depends(READ)):
 with SessionLocal() as db:
  q=base(principal.active_tenant_id)
  if status!="all":q=q.where(AssetAnnotationModel.status==status)
  rows=db.execute(q.order_by(AssetAnnotationModel.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
  ids=[r[0].id for r in rows];counts=dict(db.execute(select(AssetAnnotationModel.parent_annotation_id,func.count()).where(AssetAnnotationModel.tenant_id==principal.active_tenant_id,AssetAnnotationModel.parent_annotation_id.in_(ids)).group_by(AssetAnnotationModel.parent_annotation_id)).all()) if ids else {}
  return {"items":[rowdoc(*r,counts.get(r[0].id,0)) for r in rows],"page":page,"page_size":page_size}
@router.get("/issues/{annotation_id}")
def detail(annotation_id:str,principal:CurrentPrincipal=Depends(READ)):
 with SessionLocal() as db:
  r=db.execute(base(principal.active_tenant_id).where(AssetAnnotationModel.id==annotation_id)).first()
  if not r:raise HTTPException(404,{"code":"review_issue_not_found"})
  issue=rowdoc(*r);replies=db.execute(select(AssetAnnotationModel,PublicShareGuestModel).join(PublicShareGuestModel,(PublicShareGuestModel.tenant_id==AssetAnnotationModel.tenant_id)&(PublicShareGuestModel.id==AssetAnnotationModel.guest_id)).where(AssetAnnotationModel.tenant_id==principal.active_tenant_id,AssetAnnotationModel.parent_annotation_id==annotation_id,AssetAnnotationModel.share_id==r[0].share_id,AssetAnnotationModel.asset_id==r[0].asset_id,AssetAnnotationModel.source_asset_id==r[0].source_asset_id).order_by(AssetAnnotationModel.created_at)).all();issue["replies"]=[{"id":x.id,"content_json":x.content_json,"plain_text":x.plain_text,"created_at":x.created_at,"author":{"display_name":g.display_name}} for x,g in replies];return issue
@router.get("/stats")
def stats(principal:CurrentPrincipal=Depends(READ)):
 with SessionLocal() as db:
  q=select(AssetAnnotationModel).where(AssetAnnotationModel.tenant_id==principal.active_tenant_id,AssetAnnotationModel.parent_annotation_id.is_(None)).subquery();total=db.scalar(select(func.count()).select_from(q)) or 0;open_=db.scalar(select(func.count()).select_from(q).where(q.c.status=="open")) or 0;resolved=db.scalar(select(func.count()).select_from(q).where(q.c.status=="resolved")) or 0;assets=db.scalar(select(func.count(func.distinct(q.c.asset_id))).where(q.c.status=="open")) or 0;shares=db.scalar(select(func.count(func.distinct(q.c.share_id))).where(q.c.status=="open")) or 0;return {"total_issues":total,"open_issues":open_,"resolved_issues":resolved,"resolution_rate":0 if not total else round(resolved*100/total),"assets_with_open_issues":assets,"shares_with_open_issues":shares}
