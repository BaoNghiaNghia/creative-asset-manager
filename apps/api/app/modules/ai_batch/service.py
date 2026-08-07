from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.contracts import (
    AiBatchResultsInput, AiBatchStatusInput, AiBatchSubmissionInput,
    AiProviderError, AssetStorageProvider, OpenStoredAssetInput,
)
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_batch.model import AiBatchJobModel, AiBatchItemModel, BATCH_TERMINAL_STATUSES
from app.modules.ai_batch.repository import AiBatchRepository
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.repository import AiGovernanceRepository, MissingCostRateError, ProviderGovernanceBlocked
from app.modules.ai_governance.service import AiBudgetService, usage_units
from app.modules.ai_metadata.analysis_image import AnalysisImageLimits, AnalysisImagePreparer
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.result_importer import AiAnalysisResultImporter
from app.modules.processing.repository import ProcessingRepository
from app.modules.storage.repository import ManagedStorageRepository

def utcnow(): return datetime.now(timezone.utc)

class AiBatchService:
    def __init__(self,session:Session,settings:Settings,provider_registry:AiProviderRegistry,
                 storage_provider:AssetStorageProvider):
        self.session=session;self.settings=settings;self.provider_registry=provider_registry
        self.storage_provider=storage_provider
        self.repository=AiBatchRepository(session)
        self.governance=AiGovernanceRepository(session)

    def create_batches(self,*,tenant_id:str,analysis_ids:Sequence[str]|None=None)->list[AiBatchJobModel]:
        groups=self.repository.group_candidates(
            tenant_id=tenant_id,analysis_ids=analysis_ids,
            minimum_age_seconds=self.settings.AI_BATCH_MINIMUM_AGE_SECONDS,
            max_items=self.settings.AI_BATCH_MAX_ITEMS)
        batches=[]
        for group in groups:
            provider=self._provider(group[0].ai_provider or "gemini")
            limit=min(
                self.settings.AI_BATCH_MAX_ITEMS,
                int(getattr(provider,"batch_max_items",self.settings.AI_BATCH_MAX_ITEMS)))
            for offset in range(0,len(group),max(1,limit)):
                chunk=group[offset:offset+max(1,limit)]
                digest=hashlib.sha256(
                    "|".join(sorted(value.id for value in chunk)).encode()).hexdigest()
                batch=self.repository.create_batch(chunk,submission_key=f"batch:{digest}")
                ProcessingRepository(self.session).create_job(
                    tenant_id=tenant_id,job_type="ai_batch_submit",
                    entity_type="ai_batch_job",entity_id=batch.id,
                    idempotency_key=f"ai-batch-submit:{batch.id}",
                    payload={"batch_id":batch.id},provider_key=batch.provider,
                    provider_scope="ai")
                batches.append(batch)
        return batches

    async def submit(self,*,tenant_id:str,batch_id:str)->AiBatchJobModel:
        batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
        provider=self._provider(batch.provider)
        if batch.provider_batch_id:
            return batch
        if batch.cancellation_requested:
            return await self.cancel(tenant_id=tenant_id,batch_id=batch_id)
        if not provider.supports_batch:
            raise AiProviderError("AI provider does not support batch analysis.",
                                  code="batch_not_supported",retryable=False)
        profile=AiMetadataRepository(self.session).get_profile(batch.metadata_profile_id)
        items=self.repository.items(batch,{"pending","prepared","budget_blocked"})
        if not items:
            batch.status="partial_failed";batch.completed_at=utcnow();return batch
        preparer=AnalysisImagePreparer(self.storage_provider,limits=AnalysisImageLimits(
            max_source_bytes=self.settings.AI_ANALYSIS_MAX_SOURCE_BYTES,
            max_source_width=self.settings.AI_ANALYSIS_MAX_SOURCE_WIDTH,
            max_source_height=self.settings.AI_ANALYSIS_MAX_SOURCE_HEIGHT,
            max_output_bytes=self.settings.AI_ANALYSIS_MAX_OUTPUT_BYTES,
            max_width=self.settings.AI_ANALYSIS_MAX_WIDTH,
            max_height=self.settings.AI_ANALYSIS_MAX_HEIGHT,
            max_pixels=self.settings.AI_ANALYSIS_MAX_PIXELS,
            max_decode_pixels=self.settings.AI_ANALYSIS_MAX_DECODE_PIXELS,
            jpeg_quality=self.settings.AI_ANALYSIS_JPEG_QUALITY))
        try:
            self.governance.assert_provider_allowed(tenant_id, batch.provider, "batch")
            rate=self.governance.require_cost_rate(batch.provider,batch.model,"batch")
        except (MissingCostRateError, ProviderGovernanceBlocked) as exc:
            code=getattr(exc,"code","missing_cost_rate")
            for item in self.repository.items(batch,{"pending","prepared","budget_blocked"}):
                item.status="budget_blocked";item.last_error_code=code;item.last_error_message=str(exc)
                AiMetadataRepository(self.session).mark_budget_blocked(item.analysis_id,code=code,reason=str(exc))
            batch.status="preparing";batch.last_error_code=code;batch.last_error_message=str(exc)
            self.governance.event(tenant_id,code,reason=str(exc),details={"provider":batch.provider,"processing_mode":"batch","model":batch.model,"mode":"batch"})
            AI_METRICS.increment("budget_blocks",provider=batch.provider,mode="batch",outcome=code)
            self.session.flush();raise AiProviderError(str(exc),code=code,retryable=True)
        currency=rate.currency
        path=None
        provider_invoked=False
        try:
            handle=tempfile.NamedTemporaryFile(prefix="cam-ai-batch-",suffix=".jsonl",delete=False)
            path=handle.name;os.chmod(path,0o600)
            digest=hashlib.sha256();total=0;prepared_items=[]
            with handle:
                for item in items:
                    analysis=AiMetadataRepository(self.session).get_analysis(item.analysis_id)
                    if analysis.status=="completed":
                        item.status="completed";item.result_received=True
                        continue
                    prompt=profile.prompt_template.replace("{{ asset }}",item.asset_id)
                    if item.status=="prepared" and item.budget_reservation_id:
                        operation=item.budget_operation_key
                    else:
                        operation=f"batch:{batch.id}:provider:{batch.provider}:model:{batch.model}:mode:batch:item:{item.custom_item_id}:attempt:{item.attempt_count+1}"
                        estimate=self.governance.estimate_cost(
                            rate,max(1,(len(prompt)+3)//4),
                            self.settings.AI_ESTIMATED_OUTPUT_UNITS,1)
                        decision=AiBudgetService(self.governance,self.settings).reserve(
                            tenant_id=tenant_id,operation_key=operation,
                            estimated_cost_micros=estimate,analysis_id=item.analysis_id,
                            pilot_run_id=None,currency=currency,provider=batch.provider,model=batch.model,processing_mode="batch",operation_item_id=item.custom_item_id,attempt_number=item.attempt_count+1)
                        item.budget_operation_key=operation
                        item.budget_reservation_id=decision.reservation_id
                        item.estimated_cost_micros=estimate
                        item.attempt_count+=1
                        if not decision.allowed:
                            item.status="budget_blocked"
                            item.last_error_code=decision.code
                            item.last_error_message=decision.reason
                            AiMetadataRepository(self.session).mark_budget_blocked(
                                item.analysis_id,code=decision.code or "budget_blocked",
                                reason=decision.reason or "AI batch budget blocked.")
                            continue
                        analysis=AiMetadataRepository(self.session).mark_running(item.analysis_id)
                    storage=ManagedStorageRepository(self.session).get(
                        tenant_id,item.asset_id,"google_drive_managed")
                    if storage is None or storage.status!="stored" or not storage.remote_file_id:
                        item.status="failed";item.last_error_code="managed_asset_not_stored"
                        item.last_error_message="Managed asset is required for batch analysis."
                        item.error_json={"retryable": True}
                        if item.budget_reservation_id:
                            AiBudgetService(self.governance,self.settings).reconcile(
                                item.budget_reservation_id,0)
                        self.governance.record_usage(
                            tenant_id=tenant_id,operation_key=operation,
                            values={"asset_id":item.asset_id,"analysis_id":item.analysis_id,
                                "provider":batch.provider,"processing_mode":"batch","model":batch.model,
                                "metadata_profile":batch.metadata_profile,
                                "metadata_profile_version":batch.metadata_profile_version,
                                "prompt_version":batch.prompt_version,"input_units":0,
                                "output_units":0,"media_units":0,
                                "locally_estimated_cost_micros":0,"currency":currency,
                                "latency_ms":0,"outcome":"cancelled",
                                "retry_count":max(0,item.attempt_count-1)})
                        continue
                    image=await preparer.prepare(OpenStoredAssetInput(
                        tenant_id=tenant_id,asset_id=item.asset_id,
                        remote_file_id=storage.remote_file_id))
                    row={"custom_item_id":item.custom_item_id,"prompt":prompt,
                         "image_mime_type":image.mime_type,
                         "image_base64":base64.b64encode(image.content).decode("ascii"),
                         "metadata_profile":batch.metadata_profile,
                         "metadata_profile_version":batch.metadata_profile_version,
                         "json_schema":profile.optional_json_schema}
                    encoded=(json.dumps(row,separators=(",",":"))+"\n").encode()
                    total+=len(encoded)
                    max_bytes=min(
                        self.settings.AI_BATCH_MAX_REQUEST_BYTES,
                        int(getattr(provider,"batch_max_request_bytes",
                                    self.settings.AI_BATCH_MAX_REQUEST_BYTES)))
                    if total>max_bytes:
                        raise AiProviderError("AI batch request byte limit exceeded.",
                            code="batch_request_too_large",retryable=False)
                    handle.write(encoded);digest.update(encoded)
                    item.status="prepared";prepared_items.append(item)
            if not prepared_items:
                self.repository.counts(batch);batch.status="partial_failed"
                batch.completed_at=utcnow();self.session.flush();return batch
            batch.status="submitting";batch.submission_attempt+=1
            batch.input_checksum=digest.hexdigest();batch.input_bytes=total
            batch.estimated_cost_micros=sum(value.estimated_cost_micros for value in prepared_items)
            batch.currency=currency;self.session.commit()
            self.governance.assert_provider_allowed(tenant_id, batch.provider, "batch")
            provider_invoked=True
            submission=await provider.submit_batch(AiBatchSubmissionInput(
                tenant_id=tenant_id,submission_key=batch.submission_key,
                display_name=f"cam-{batch.id}",model=batch.model,input_path=path,
                item_count=len(prepared_items),total_bytes=total))
            batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
            batch.provider_batch_id=submission.provider_batch_id
            batch.provider_request_id=submission.provider_request_id
            if submission.provider_metadata:
                batch.error_json={
                    "provider_metadata":dict(submission.provider_metadata)}
            batch.status="submitted";batch.submitted_at=utcnow()
            for item in self.repository.items(batch,{"prepared"}):
                item.status="submitted";item.submitted_at=utcnow()
            self._enqueue_poll(batch)
            self.session.flush();return batch
        except AiProviderError as exc:
            batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
            batch.status="ambiguous" if exc.code.endswith("ambiguous") else "failed"
            batch.last_error_code=exc.code;batch.last_error_message=str(exc)
            batch.error_json={"retryable":exc.retryable}
            provider_metadata=getattr(exc,"provider_metadata",None)
            if isinstance(provider_metadata,dict):
                batch.error_json={**batch.error_json,
                    "provider_metadata":dict(provider_metadata)}
            # Local preparation failures cannot consume provider budget. Permanent
            # provider rejections are also non-billable. Preserve reservations only
            # for retryable or ambiguous external outcomes because the provider may
            # have accepted the batch before the response was lost.
            if not provider_invoked or not exc.retryable:
                for item in self.repository.items(batch):
                    if item.status in {"completed","budget_blocked"}:
                        continue
                    if item.budget_reservation_id:
                        AiBudgetService(self.governance,self.settings).reconcile(
                            item.budget_reservation_id,0)
                    item.status="failed"
                    item.last_error_code=exc.code
                    item.last_error_message=str(exc)
                    item.error_json={"retryable":exc.retryable}
                    AiMetadataRepository(self.session).fail_analysis(
                        item.analysis_id,error_code=exc.code,
                        error_message=str(exc),retryable=exc.retryable,
                        terminal=not exc.retryable)
            self.session.flush();raise
        finally:
            if path:
                try: Path(path).unlink(missing_ok=True)
                except OSError: pass

    async def poll(self,*,tenant_id:str,batch_id:str)->AiBatchJobModel:
        batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
        provider=self._provider(batch.provider)
        if batch.status in BATCH_TERMINAL_STATUSES:return batch
        if batch.cancellation_requested:return await self.cancel(tenant_id=tenant_id,batch_id=batch_id)
        if not batch.provider_batch_id:
            raise AiProviderError("Batch provider identity is not known.",
                                  code="batch_submission_ambiguous",retryable=True)
        status=await provider.get_batch_status(AiBatchStatusInput(
            tenant_id=tenant_id,provider_batch_id=batch.provider_batch_id))
        batch.poll_attempt+=1;batch.usage_json=dict(status.usage)
        state=status.state.lower()
        if state in {"pending","submitted","running"}:
            batch.status="running" if state=="running" else "submitted"
            delay=max(self.settings.AI_BATCH_POLL_INTERVAL_SECONDS,
                      status.retry_after_seconds or 0)
            batch.next_poll_at=utcnow()+timedelta(seconds=delay)
            self._enqueue_poll(batch)
        elif state=="completed":
            batch.status="importing"
            ProcessingRepository(self.session).create_job(
                tenant_id=tenant_id,job_type="ai_batch_import",
                entity_type="ai_batch_job",entity_id=batch.id,
                idempotency_key=f"ai-batch-import:{batch.id}:{batch.import_attempt+1}",
                payload={"batch_id":batch.id},provider_key=batch.provider,
                provider_scope="ai")
        elif state in {"failed","expired","cancelled"}:
            batch.status=state
            batch.last_error_code=status.error_code
            batch.last_error_message=status.error_message
            batch.completed_at=utcnow()
            item_status="cancelled" if state=="cancelled" else "failed"
            for item in self.repository.items(batch,{"submitted","prepared"}):
                item.status=item_status
                item.completed_at=utcnow()
                item.last_error_code=status.error_code or f"batch_{state}"
                item.last_error_message=status.error_message or (
                    f"Provider batch reached terminal state {state}.")
                item.error_json={"retryable":state in {"failed","expired"}}
                AiMetadataRepository(self.session).fail_analysis(
                    item.analysis_id,error_code=item.last_error_code,
                    error_message=item.last_error_message,
                    retryable=state in {"failed","expired"},
                    terminal=state=="cancelled")
                self._reconcile_item(batch,item,None,provider_failed=True)
            self.repository.counts(batch)
            self._reconcile_batch_total(batch)
            if state in {"failed","expired"}:
                ProcessingRepository(self.session).create_job(
                    tenant_id=tenant_id,job_type="ai_batch_retry_items",
                    entity_type="ai_batch_job",entity_id=batch.id,
                    idempotency_key=f"ai-batch-retry:{batch.id}:terminal",
                    payload={"batch_id":batch.id},provider_key=batch.provider,
                    provider_scope="ai")
        else:
            raise AiProviderError("Unknown batch provider state.",
                                  code="batch_unknown_state",retryable=True)
        self.session.flush();return batch

    async def import_results(self,*,tenant_id:str,batch_id:str)->AiBatchJobModel:
        batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
        provider=self._provider(batch.provider)
        if batch.status in BATCH_TERMINAL_STATUSES:return batch
        if not batch.provider_batch_id:raise LookupError("provider batch ID missing")
        batch.status="importing";batch.import_attempt+=1;self.session.commit()
        sequence=int(batch.result_cursor or "-1")
        clean_end=False
        try:
            async for entry in provider.stream_batch_results(AiBatchResultsInput(
                tenant_id=tenant_id,provider_batch_id=batch.provider_batch_id,
                cursor=batch.result_cursor)):
                sequence+=1
                batch=self.repository.get_batch(tenant_id,batch_id)
                item=self.repository.item_by_custom(batch,entry.custom_item_id)
                if item is None:
                    errors=dict(batch.error_json or {})
                    unknown=list(errors.get("unknown_custom_item_ids") or [])
                    if entry.custom_item_id not in unknown:unknown.append(entry.custom_item_id)
                    errors["unknown_custom_item_ids"]=unknown[:100]
                    batch.error_json=errors;batch.result_cursor=str(sequence)
                    self.session.commit();continue
                if item.result_received:
                    batch.result_cursor=str(sequence);self.session.commit();continue
                item.provider_item_id=entry.provider_item_id
                item.result_received=True;item.result_sequence=sequence
                if entry.result is None:
                    item.status="failed";item.last_error_code=entry.error_code or "provider_item_failed"
                    item.last_error_message=entry.error_message
                    item.error_json={"retryable":entry.retryable}
                    AiMetadataRepository(self.session).fail_analysis(
                        item.analysis_id,error_code=item.last_error_code,
                        error_message=entry.error_message or "Provider rejected batch item.",
                        retryable=entry.retryable,terminal=not entry.retryable)
                    self._reconcile_item(batch,item,None,provider_failed=True)
                else:
                    imported=AiAnalysisResultImporter(
                        self.session,self.settings).import_result(
                            tenant_id=tenant_id,analysis_id=item.analysis_id,
                            result=entry.result)
                    if imported.status=="completed":
                        item.status="completed";item.completed_at=utcnow()
                    else:
                        item.status="failed";item.last_error_code="metadata_validation_failed"
                        item.last_error_message="AI metadata failed validation."
                        item.error_json={"retryable":True,"validation_errors":list(imported.validation_errors)}
                    self._reconcile_item(batch,item,entry.result)
                batch.result_cursor=str(sequence)
                self.repository.counts(batch);self.session.commit()
            clean_end=True
        finally:
            if not clean_end:
                self.session.rollback()
        batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
        if clean_end:
            for item in self.repository.items(batch,{"submitted","prepared"}):
                item.status="missing";item.last_error_code="batch_result_missing"
                item.last_error_message="Provider result did not contain this custom item ID."
                item.error_json={"retryable":True}
                AiMetadataRepository(self.session).fail_analysis(
                    item.analysis_id,error_code="batch_result_missing",
                    error_message="Provider batch result was missing.",
                    retryable=True,terminal=False)
                self._reconcile_item(batch,item,None,provider_failed=True)
            self.repository.counts(batch)
            unresolved=self.repository.items(batch,{"failed","missing","budget_blocked"})
            batch.status="completed" if not unresolved else "partial_failed"
            batch.completed_at=utcnow()
            self._reconcile_batch_total(batch)
            if unresolved:
                ProcessingRepository(self.session).create_job(
                    tenant_id=tenant_id,job_type="ai_batch_retry_items",
                    entity_type="ai_batch_job",entity_id=batch.id,
                    idempotency_key=f"ai-batch-retry:{batch.id}:{batch.import_attempt}",
                    payload={"batch_id":batch.id},provider_key=batch.provider,
                    provider_scope="ai")
            self.session.flush()
        return batch

    def _reconcile_item(self,batch:AiBatchJobModel,item:AiBatchItemModel,result,
                        provider_failed:bool=False)->None:
        input_units=output_units=media_units=0
        provider_cost=None;model=batch.model;request_id=None
        if result is not None:
            input_units,output_units,media_units=usage_units(result.usage)
            raw=result.usage.get("costMicros")
            provider_cost=max(0,int(raw)) if isinstance(raw,(int,float)) else None
            model=result.model or model;request_id=result.provider_request_id
        rate=self.governance.require_cost_rate(batch.provider,model,"batch")
        local=self.governance.estimate_cost(rate,input_units,output_units,media_units)
        actual=provider_cost if provider_cost is not None else (
            item.estimated_cost_micros if provider_failed else local)
        if item.budget_reservation_id:
            AiBudgetService(self.governance,self.settings).reconcile(
                item.budget_reservation_id,actual)
        item.actual_cost_micros=actual
        item.usage_json={"input_units":input_units,"output_units":output_units,
                         "media_units":media_units}
        self.governance.record_usage(
            tenant_id=batch.tenant_id,operation_key=item.budget_operation_key,
            values={"asset_id":item.asset_id,"analysis_id":item.analysis_id,
                "provider":batch.provider,"processing_mode":"batch","model":model,
                "metadata_profile":batch.metadata_profile,
                "metadata_profile_version":batch.metadata_profile_version,
                "prompt_version":batch.prompt_version,"input_units":input_units,
                "output_units":output_units,"media_units":media_units,
                "provider_reported_cost_micros":provider_cost,
                "locally_estimated_cost_micros":local,
                "currency":rate.currency if rate else batch.currency,
                "latency_ms":0,
                "outcome":"provider_failed" if provider_failed else (
                    "completed" if item.status=="completed" else "invalid_metadata"),
                "retry_count":max(0,item.attempt_count-1),
                "provider_request_id":request_id})
        AI_METRICS.increment("batch_count",provider=batch.provider,mode="batch",outcome="completed" if item.status=="completed" else "failed")

    def _reconcile_batch_total(self,batch:AiBatchJobModel)->None:
        raw=(batch.usage_json or {}).get("costMicros")
        items=[item for item in self.repository.items(batch) if item.budget_reservation_id]
        if not isinstance(raw,(int,float)) or not items:
            batch.actual_cost_micros=sum(item.actual_cost_micros for item in items)
            return
        total=max(0,int(raw));weight=sum(max(1,item.estimated_cost_micros) for item in items)
        allocated=0
        for index,item in enumerate(items):
            amount=(total-allocated) if index==len(items)-1 else (
                total*max(1,item.estimated_cost_micros)//weight)
            allocated+=amount
            AiBudgetService(self.governance,self.settings).reconcile(
                item.budget_reservation_id,amount)
            item.actual_cost_micros=amount
            if item.budget_operation_key:
                self.governance.record_usage(
                    tenant_id=batch.tenant_id,operation_key=item.budget_operation_key,
                    values={"provider_reported_cost_micros":amount})
        batch.actual_cost_micros=total

    async def cancel(self,*,tenant_id:str,batch_id:str)->AiBatchJobModel:
        batch=self.repository.get_batch(tenant_id,batch_id,for_update=True)
        provider=self._provider(batch.provider)
        batch.cancellation_requested=True
        if batch.provider_batch_id and batch.status not in BATCH_TERMINAL_STATUSES:
            await provider.cancel_batch(AiBatchStatusInput(
                tenant_id=tenant_id,provider_batch_id=batch.provider_batch_id))
        for item in self.repository.items(batch):
            if item.status!="completed":
                item.status="cancelled";item.completed_at=utcnow()
                AiMetadataRepository(self.session).fail_analysis(
                    item.analysis_id,error_code="batch_cancelled",
                    error_message="AI batch was cancelled.",retryable=True,
                    terminal=False)
                if item.budget_reservation_id:
                    AiBudgetService(self.governance,self.settings).reconcile(
                        item.budget_reservation_id,0)
        batch.status="cancelled";batch.completed_at=utcnow()
        self.repository.counts(batch);self.session.flush();return batch

    def retry_items(self,*,tenant_id:str,batch_id:str)->list[AiBatchJobModel]:
        batch=self.repository.get_batch(tenant_id,batch_id)
        self._provider(batch.provider)
        retryable=[item for item in self.repository.items(
            batch,{"failed","missing","budget_blocked"})
            if item.attempt_count<self.settings.AI_BATCH_MAX_ITEM_ATTEMPTS
            and (item.status in {"missing","budget_blocked"} or
                 bool((item.error_json or {}).get("retryable")))]
        if not retryable:return []
        analyses=[]
        metadata=AiMetadataRepository(self.session)
        for item in retryable:
            analysis=metadata.create_analysis(
                tenant_id=tenant_id,asset_id=item.asset_id,
                metadata_profile_id=batch.metadata_profile_id,
                prompt_version=batch.prompt_version,
                pipeline_version=batch.pipeline_version,
                ai_provider=batch.provider,ai_model=batch.model,force=True)
            if self.settings.AI_BATCH_FALLBACK_TO_SINGLE_ENABLED:
                ProcessingRepository(self.session).create_job(
                    tenant_id=tenant_id,job_type="asset_analyze",
                    entity_type="asset_ai_analysis",entity_id=analysis.id,
                    idempotency_key=f"ai-batch-fallback:{batch.id}:{item.id}:{analysis.id}",
                    payload={"analysis_id":analysis.id},provider_key=batch.provider,
                    provider_scope="ai")
            else: analyses.append(analysis)
        if self.settings.AI_BATCH_FALLBACK_TO_SINGLE_ENABLED:return []
        digest=hashlib.sha256(
            f"{batch.id}:{batch.import_attempt}:".encode()+
            "|".join(value.id for value in analyses).encode()).hexdigest()
        child=self.repository.create_batch(analyses,submission_key=f"batch-retry:{digest}")
        ProcessingRepository(self.session).create_job(
            tenant_id=tenant_id,job_type="ai_batch_submit",
            entity_type="ai_batch_job",entity_id=child.id,
            idempotency_key=f"ai-batch-submit:{child.id}",
            payload={"batch_id":child.id},provider_key=child.provider,
            provider_scope="ai")
        return [child]

    def _enqueue_poll(self,batch:AiBatchJobModel)->None:
        ProcessingRepository(self.session).create_job(
            tenant_id=batch.tenant_id,job_type="ai_batch_poll",
            entity_type="ai_batch_job",entity_id=batch.id,
            idempotency_key=f"ai-batch-poll:{batch.id}:{batch.poll_attempt+1}",
            payload={"batch_id":batch.id},provider_key=batch.provider,
            provider_scope="ai",next_attempt_at=batch.next_poll_at or (
                utcnow()+timedelta(seconds=self.settings.AI_BATCH_POLL_INTERVAL_SECONDS)))

    def _provider(self,provider_name:str|None):
        return self.provider_registry.require(provider_name or "")
