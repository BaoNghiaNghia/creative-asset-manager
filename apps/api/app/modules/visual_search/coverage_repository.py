from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import select
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.mime_types import is_supported_image_mime_type

@dataclass(frozen=True)
class CoverageResource:
    source_asset_id:str; external_source_id:str; source_type:str; display_name:str|None
    mime_type:str|None; asset_id:str|None; content_hash:str|None; imported:bool; eligible:bool; unsupported:bool
    activity_at:datetime|None=None

class VisualCoverageResourceReader:
    """Read-only, set-based SourceAsset coverage input for visual search."""
    def __init__(self, session): self.session=session
    def resources(self, tenant_id:str)->list[CoverageResource]:
        rows=self.session.execute(select(SourceAssetModel,AssetSourceLinkModel,AssetModel,ExternalSourceModel).join(ExternalSourceModel,(ExternalSourceModel.id==SourceAssetModel.external_source_id)&(ExternalSourceModel.tenant_id==SourceAssetModel.tenant_id)).outerjoin(AssetSourceLinkModel,(AssetSourceLinkModel.source_asset_id==SourceAssetModel.id)&(AssetSourceLinkModel.tenant_id==SourceAssetModel.tenant_id)).outerjoin(AssetModel,(AssetModel.id==AssetSourceLinkModel.asset_id)&(AssetModel.tenant_id==AssetSourceLinkModel.tenant_id)).where(SourceAssetModel.tenant_id==tenant_id,SourceAssetModel.deleted_at.is_(None)).order_by(SourceAssetModel.id)).all()
        result=[]; seen=set()
        for source,link,asset,external in rows:
            if source.id in seen: continue
            seen.add(source.id)
            mime=(source.mime_type or '').lower()
            if not mime.startswith('image/'): continue
            imported=bool(link and asset and asset.tenant_id==tenant_id)
            supported=is_supported_image_mime_type(source.mime_type)
            valid_hash=bool(asset and isinstance(asset.content_hash,str) and len(asset.content_hash)==64)
            activity_values=[
                value for value in (
                    source.source_modified_at,
                    source.last_seen_at,
                    source.updated_at,
                    asset.updated_at if asset is not None else None,
                )
                if value is not None
            ]
            activity_at=max(activity_values) if activity_values else None
            result.append(CoverageResource(source.id,source.external_source_id,external.source_type,external.display_name,source.mime_type,asset.id if imported else None,asset.content_hash if imported else None,imported,imported and supported and valid_hash,not supported,activity_at))
        return result