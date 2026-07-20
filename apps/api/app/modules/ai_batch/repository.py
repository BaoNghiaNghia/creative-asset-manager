from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetModel

def utcnow(): return datetime.now(timezone.utc)

@dataclass(frozen=True,slots=True)
class BatchCompatibilityKey:
    tenant_id: str
    provider: str
    model: str
    metadata_profile_id: str
    metadata_profile: str
    metadata_profile_version: str
    prompt_version: str
    pipeline_version: str
    input_family: str

class AiBatchRepository:
    def __init__(self,session:Session): self.session=session

    @staticmethod
    def compatibility(analysis:AssetAiAnalysisModel,asset:AssetModel)->BatchCompatibilityKey:
        mime=(asset.mime_type or "").lower()
        family="image" if mime.startswith("image/") else mime or "unknown"
        return BatchCompatibilityKey(
            analysis.tenant_id,analysis.ai_provider or "gemini",
            analysis.ai_model or "gemini-2.5-flash",analysis.metadata_profile_id,
            analysis.metadata_profile,analysis.metadata_profile_version,
            analysis.prompt_version,analysis.pipeline_version,family,
        )

    def group_candidates(self,*,tenant_id:str,analysis_ids:Sequence[str]|None=None,
                         minimum_age_seconds:int=0,max_items:int=100)->list[list[AssetAiAnalysisModel]]:
        threshold=utcnow()-timedelta(seconds=max(0,minimum_age_seconds))
        statement=select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id==tenant_id,
            AssetAiAnalysisModel.status=="pending",
            AssetAiAnalysisModel.created_at<=threshold,
        )
        if analysis_ids is not None: statement=statement.where(AssetAiAnalysisModel.id.in_(analysis_ids))
        statement=statement.where(
            (AssetAiAnalysisModel.processing_stage.is_(None)) |
            (~AssetAiAnalysisModel.processing_stage.in_(("batch_queued","batch_submitted")))
        )
        statement=statement.order_by(AssetAiAnalysisModel.created_at,AssetAiAnalysisModel.id)
        if self.session.get_bind().dialect.name=="postgresql":
            statement=statement.with_for_update(skip_locked=True)
        candidates=list(self.session.scalars(statement).all())
        grouped:dict[BatchCompatibilityKey,list[AssetAiAnalysisModel]]={}
        for analysis in candidates:
            asset=self.session.get(AssetModel,analysis.asset_id)
            if asset is None or asset.tenant_id!=tenant_id or not (asset.mime_type or "").startswith("image/"):
                continue
            grouped.setdefault(self.compatibility(analysis,asset),[]).append(analysis)
        result=[]
        for key in sorted(grouped,key=lambda value:repr(value)):
            values=grouped[key]
            for index in range(0,len(values),max(1,max_items)):
                result.append(values[index:index+max(1,max_items)])
        return result

    def create_batch(self,analyses:Sequence[AssetAiAnalysisModel],*,submission_key:str)->AiBatchJobModel:
        if not analyses: raise ValueError("batch requires at least one analysis")
        first=analyses[0];asset=self.session.get(AssetModel,first.asset_id)
        key=self.compatibility(first,asset)
        for analysis in analyses[1:]:
            candidate_asset=self.session.get(AssetModel,analysis.asset_id)
            if self.compatibility(analysis,candidate_asset)!=key:
                raise ValueError("incompatible batch analysis")
        existing=self.session.scalar(select(AiBatchJobModel).where(
            AiBatchJobModel.tenant_id==key.tenant_id,
            AiBatchJobModel.submission_key==submission_key))
        if existing is not None: return existing
        batch=AiBatchJobModel(
            tenant_id=key.tenant_id,submission_key=submission_key,
            provider=key.provider,model=key.model,
            metadata_profile_id=key.metadata_profile_id,
            metadata_profile=key.metadata_profile,
            metadata_profile_version=key.metadata_profile_version,
            prompt_version=key.prompt_version,pipeline_version=key.pipeline_version,
            item_count=len(analyses),
        )
        self.session.add(batch);self.session.flush()
        for analysis in analyses:
            analysis.processing_stage="batch_queued"
            analysis.updated_at=utcnow()
            custom=hashlib.sha256(
                f"{batch.id}:{analysis.id}:{analysis.content_hash}".encode()
            ).hexdigest()[:48]
            self.session.add(AiBatchItemModel(
                tenant_id=batch.tenant_id,batch_job_id=batch.id,
                custom_item_id=custom,asset_id=analysis.asset_id,
                analysis_id=analysis.id,
            ))
        self.session.flush();return batch

    def get_batch(self,tenant_id:str,batch_id:str,*,for_update:bool=False)->AiBatchJobModel:
        statement=select(AiBatchJobModel).where(
            AiBatchJobModel.tenant_id==tenant_id,AiBatchJobModel.id==batch_id)
        if for_update and self.session.get_bind().dialect.name=="postgresql":
            statement=statement.with_for_update()
        value=self.session.scalar(statement)
        if value is None: raise LookupError(batch_id)
        return value

    def items(self,batch:AiBatchJobModel,statuses:Iterable[str]|None=None)->list[AiBatchItemModel]:
        statement=select(AiBatchItemModel).where(
            AiBatchItemModel.tenant_id==batch.tenant_id,
            AiBatchItemModel.batch_job_id==batch.id)
        if statuses is not None: statement=statement.where(AiBatchItemModel.status.in_(tuple(statuses)))
        return list(self.session.scalars(statement.order_by(AiBatchItemModel.id)).all())

    def item_by_custom(self,batch:AiBatchJobModel,custom_item_id:str)->AiBatchItemModel|None:
        return self.session.scalar(select(AiBatchItemModel).where(
            AiBatchItemModel.tenant_id==batch.tenant_id,
            AiBatchItemModel.batch_job_id==batch.id,
            AiBatchItemModel.custom_item_id==custom_item_id))

    def counts(self,batch:AiBatchJobModel)->None:
        items=self.items(batch)
        batch.completed_count=sum(item.status=="completed" for item in items)
        batch.failed_count=sum(item.status in {"failed","budget_blocked"} for item in items)
        batch.missing_count=sum(item.status=="missing" for item in items)
        batch.updated_at=utcnow();self.session.flush()
