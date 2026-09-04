from __future__ import annotations
import hashlib
from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.explorer.cache import invalidate_drive_listings,invalidate_drive_source
from app.modules.authorization.folder_scope_cache import viewer_folder_hierarchy_cache,viewer_folder_remote_parent_cache
from app.providers.microsoft.onedrive import OneDriveClient

def source_key(provider_account_id:str,drive_id:str)->str:
 raw=f"onedrive:{provider_account_id}:{drive_id}"
 return raw if len(raw)<=255 else "onedrive:"+hashlib.sha256(raw.encode()).hexdigest()

async def register_onedrive_source(*,tenant_id:str,connection,profile:dict,access_token:str,reconnect_source_id:str|None=None):
 async with OneDriveClient(access_token) as graph: drive=await graph.drive()
 drive_id=str(drive.get("id") or "")
 if not drive_id: raise ValueError("Microsoft OneDrive has no stable drive identity")
 account_id=str(connection.provider_account_id or profile.get("id") or "")
 if not account_id: raise ValueError("Microsoft OneDrive has no account identity")
 with SessionLocal() as session:
  existing=None
  if reconnect_source_id:
   existing=session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.id==reconnect_source_id,ExternalSourceModel.tenant_id==tenant_id,ExternalSourceModel.source_type=="onedrive"))
   if existing is None: raise ValueError("OneDrive source is unavailable")
   existing_metadata=existing.source_metadata or {}
   old=str(existing_metadata.get("drive_id") or "")
   if old and old!=drive_id: raise ValueError("OneDrive reconnect drive identity mismatch")
   old_account=str(existing_metadata.get("provider_account_id") or "")
   if old_account and old_account!=account_id: raise ValueError("OneDrive reconnect account identity mismatch")
  if existing is None:
   existing=session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id==tenant_id,ExternalSourceModel.source_type=="onedrive",ExternalSourceModel.source_key==source_key(account_id,drive_id)))
  metadata={"provider_account_id":account_id,"account_email":connection.account_email,"drive_id":drive_id,"drive_type":drive.get("driveType"),"drive_name":drive.get("name"),"web_url":drive.get("webUrl")}
  source=AssetRegistryRepository(session).upsert_external_source(tenant_id=tenant_id,source_key=existing.source_key if existing else source_key(account_id,drive_id),source_type="onedrive",display_name=drive.get("name") or connection.account_email or "OneDrive",source_metadata=metadata,oauth_connection_id=connection.id,status="active")
  session.commit()
 viewer_folder_hierarchy_cache.invalidate(tenant_id=tenant_id,external_source_id=source.id);viewer_folder_remote_parent_cache.invalidate(tenant_id=tenant_id,external_source_id=source.id);invalidate_drive_source(tenant_id=tenant_id,external_source_id=source.id);invalidate_drive_listings(tenant_id=tenant_id,external_source_id=source.id)
 return source
