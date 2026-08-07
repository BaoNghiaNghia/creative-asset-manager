from __future__ import annotations
import argparse
from sqlalchemy import func, select
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import OAuthConnectionModel

def main():
 p=argparse.ArgumentParser(); p.add_argument("--tenant-id",required=True); p.add_argument("--provider-account-id",required=True); p.add_argument("--apply",action="store_true"); a=p.parse_args()
 with SessionLocal() as db:
  sources=list(db.scalars(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id==a.tenant_id,ExternalSourceModel.source_type=="google_drive")).all())
  matching=[s for s in sources if str((s.source_metadata or {}).get("provider_account_id"))==a.provider_account_id]
  counts=dict(db.execute(select(SourceAssetModel.external_source_id,func.count(SourceAssetModel.id)).where(SourceAssetModel.tenant_id==a.tenant_id).group_by(SourceAssetModel.external_source_id)).all())
  canonical=max(matching,key=lambda s:(int(counts.get(s.id,0)),bool((s.source_metadata or {}).get("is_default")),s.created_at,s.id)) if matching else None
  connections=list(db.scalars(select(OAuthConnectionModel).where(OAuthConnectionModel.tenant_id==a.tenant_id,OAuthConnectionModel.provider=="google")).all())
  active=next((c for c in connections if a.provider_account_id==c.provider_account_id and c.status=="active" and "https://www.googleapis.com/auth/drive.readonly" in set(c.scopes_json or ())),None)
  print({"tenant_id":a.tenant_id,"canonical_source_id":canonical.id if canonical else None,"duplicate_source_ids":[s.id for s in matching if not canonical or s.id!=canonical.id],"active_connection_id":active.id if active else None,"dry_run":not a.apply})
  if not a.apply or not canonical or not active: return
  metadata=dict(canonical.source_metadata or {}); metadata.update({"oauth_connection_id":active.id,"provider_account_id":a.provider_account_id,"is_default":True}); canonical.source_metadata=metadata
  for source in sources:
   if source.id!=canonical.id:
    m=dict(source.source_metadata or {}); m["is_default"]=False; source.source_metadata=m
  db.commit()
if __name__=="__main__": main()
