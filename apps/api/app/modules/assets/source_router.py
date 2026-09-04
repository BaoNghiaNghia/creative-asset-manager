from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.source_credentials import source_credential_contract
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal, require_permission
from app.modules.explorer.cache import invalidate_drive_listings, invalidate_drive_source
from app.modules.authorization.folder_scope_cache import viewer_folder_hierarchy_cache, viewer_folder_remote_parent_cache

router=APIRouter(prefix="/api/sources",tags=["sources"])
ASSETS_MANAGE=require_permission("assets.manage")

class SourceAccount(BaseModel):
    provider_account_id:str|None=None
    email:str|None=None
class SourceCapabilities(BaseModel):
    browse:bool=True
    sync:bool=True
    write:bool=False
    reconnect:bool=True
    disconnect:bool=True
class ExternalSourceSummary(BaseModel):
    id:str
    source_type:str
    display_name:str|None=None
    status:str
    provider:str
    connection_purpose:str
    account:SourceAccount
    metadata:dict
    capabilities:SourceCapabilities

def summary(source:ExternalSourceModel)->ExternalSourceSummary:
    contract=source_credential_contract(source.source_type)
    metadata=dict(source.source_metadata or {})
    return ExternalSourceSummary(id=source.id,source_type=source.source_type,display_name=source.display_name,status=source.status,provider=contract.provider,connection_purpose=contract.connection_purpose,account=SourceAccount(provider_account_id=metadata.get("provider_account_id"),email=metadata.get("account_email")),metadata={key:metadata[key] for key in ("drive_type","drive_name","web_url") if key in metadata},capabilities=SourceCapabilities(write=source.source_type=="google_drive"))

@router.get("",response_model=list[ExternalSourceSummary])
def list_sources(principal:CurrentPrincipal=Depends(require_authenticated_principal),session:Session=Depends(get_db)):
    rows=session.scalars(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id==principal.active_tenant_id).order_by(ExternalSourceModel.display_name,ExternalSourceModel.id)).all()
    return [summary(row) for row in rows]

@router.post("/{source_id}/disconnect",response_model=ExternalSourceSummary)
def disconnect_source(source_id:str,principal:CurrentPrincipal=Depends(ASSETS_MANAGE),session:Session=Depends(get_db)):
    source=session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.id==source_id,ExternalSourceModel.tenant_id==principal.active_tenant_id))
    if source is None:raise HTTPException(404,detail={"code":"source_not_found","message":"Source is unavailable"})
    if source.status=="disconnected":raise HTTPException(409,detail={"code":"source_disconnected","message":"Source is already disconnected"})
    source.status="disconnected";session.commit()
    viewer_folder_hierarchy_cache.invalidate(tenant_id=principal.active_tenant_id,external_source_id=source.id)
    viewer_folder_remote_parent_cache.invalidate(tenant_id=principal.active_tenant_id,external_source_id=source.id)
    invalidate_drive_source(tenant_id=principal.active_tenant_id,external_source_id=source.id)
    invalidate_drive_listings(tenant_id=principal.active_tenant_id,external_source_id=source.id)
    return summary(source)
