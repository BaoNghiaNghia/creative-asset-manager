import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.providers.registry import AiProviderRegistry
from app.domain.providers.contracts import (
    AiBatchResult,AiBatchStatus,AiBatchSubmission,AiMetadataAnalysisResult,
    AiProviderError,StoredAssetReadStream,
)
from app.modules.ai_batch.model import AiBatchItemModel
from app.modules.ai_batch.repository import AiBatchRepository
from app.modules.ai_batch.service import AiBatchService
from app.modules.ai_governance.model import (
    AiBudgetReservationModel,AiCostRateModel,AiUsageRecordModel,
    TenantAiBudgetPolicyModel,
)
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel
from datetime import datetime,timezone

class FakeStorage:
    def __init__(self,content):self.content=content
    async def open_asset(self,_input):
        async def body():yield self.content
        async def close():return None
        return StoredAssetReadStream(body=body(),close=close,content_type="image/png")
    async def store_asset(self,_input):raise NotImplementedError
    async def store_metadata_sidecar(self,_input):raise NotImplementedError

class FakeBatchProvider:
    supports_batch=True
    provider_name="fake"
    model="fake-1"
    def __init__(self):
        self.submit_calls=0;self.cancel_calls=0;self.paths=[]
        self.statuses=[AiBatchStatus("completed")]
        self.results=[];self.fail_stream_once=False;self._stream_failed=False
        self.ambiguous_once=False
    async def analyze_single(self,_input):raise AssertionError("single call not expected")
    async def submit_batch(self,input):
        self.submit_calls+=1;self.paths.append(input.input_path)
        if self.ambiguous_once and self.submit_calls==1:
            raise AiProviderError("ambiguous",code="fake_submission_ambiguous",retryable=True)
        return AiBatchSubmission("batches/fake","submitted","request-batch",
            {"input_file_id":"provider-input"})
    async def get_batch_status(self,_input):
        return self.statuses.pop(0) if len(self.statuses)>1 else self.statuses[0]
    async def stream_batch_results(self,input):
        start=int(input.cursor or "-1")+1
        for index,value in enumerate(self.results):
            if index<start:continue
            yield value
            if self.fail_stream_once and not self._stream_failed:
                self._stream_failed=True
                raise AiProviderError("stream failed",code="stream_failed",retryable=True)
    async def cancel_batch(self,_input):self.cancel_calls+=1;return True

class AiBatchServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:",
            connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session=Session(self.engine,expire_on_commit=False)
        image=io.BytesIO();Image.new("RGB",(8,8),"blue").save(image,format="PNG")
        self.storage=FakeStorage(image.getvalue());self.provider=FakeBatchProvider()
        self.registry=AiProviderRegistry();self.registry.register("fake",self.provider)
        self.settings=Settings(
            AI_BATCH_MINIMUM_AGE_SECONDS=0,AI_BATCH_MAX_ITEMS=10,
            AI_BATCH_MAX_REQUEST_BYTES=2_000_000,
            AI_ESTIMATED_OUTPUT_UNITS=10)
        self.profile=AiMetadataRepository(self.session).create_profile(
            tenant_id="tenant-a",profile_name="general",profile_version="1",
            prompt_template="Analyze {{ asset }}",
            optional_json_schema={"type":"object","required":["subject"],
                "properties":{"subject":{"type":"string"}}})
        self.analyses=[]
        for index in range(3):
            asset=AssetModel(tenant_id="tenant-a",content_hash=f"{index:064x}",
                mime_type="image/png",size_bytes=len(image.getvalue()))
            self.session.add(asset);self.session.flush()
            analysis=AiMetadataRepository(self.session).create_analysis(
                tenant_id="tenant-a",asset_id=asset.id,
                metadata_profile_id=self.profile.id,prompt_version="p1",
                pipeline_version="pipe1",ai_provider="fake",ai_model="fake-1")
            self.session.add(AssetStorageObjectModel(
                tenant_id="tenant-a",asset_id=asset.id,content_hash=asset.content_hash,
                storage_provider="google_drive_managed",status="stored",
                remote_file_id=f"remote-{index}",remote_folder_id="folder"))
            self.analyses.append(analysis)
        self.session.add(AiCostRateModel(
            provider="fake",model="fake-1",effective_at=datetime.now(timezone.utc),
            input_unit_cost=.000001,output_unit_cost=.000001,
            media_unit_cost=.00001,currency="USD"))
        self.session.commit()

    def tearDown(self):self.session.close();self.engine.dispose()

    def service(self):return AiBatchService(
        self.session,self.settings,self.registry,self.storage)

    async def _batch(self):
        batches=self.service().create_batches(
            tenant_id="tenant-a",analysis_ids=[value.id for value in self.analyses])
        self.assertEqual(len(batches),1)
        self.session.commit()
        return batches[0]

    async def test_grouping_rejects_incompatible_and_submission_is_idempotent(self):
        other_asset=AssetModel(tenant_id="tenant-a",content_hash="f"*64,
            mime_type="image/png")
        self.session.add(other_asset);self.session.flush()
        other=AiMetadataRepository(self.session).create_analysis(
            tenant_id="tenant-a",asset_id=other_asset.id,
            metadata_profile_id=self.profile.id,prompt_version="different",
            pipeline_version="pipe1",ai_provider="fake",ai_model="fake-1")
        groups=AiBatchRepository(self.session).group_candidates(
            tenant_id="tenant-a",analysis_ids=[self.analyses[0].id,other.id],
            minimum_age_seconds=0,max_items=10)
        self.assertEqual(sorted(len(group) for group in groups),[1,1])
        batch=await self._batch()
        await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(self.provider.submit_calls,1)
        self.assertFalse(os.path.exists(self.provider.paths[0]))
        self.assertEqual(batch.error_json["provider_metadata"]["input_file_id"],
                         "provider-input")

    async def test_submit_poll_out_of_order_partial_import_and_usage_idempotency(self):
        batch=await self._batch()
        await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        items=AiBatchRepository(self.session).items(batch)
        self.provider.statuses=[AiBatchStatus("running",retry_after_seconds=60),
                                AiBatchStatus("completed",usage={"costMicros":90})]
        first=await self.service().poll(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(first.status,"running")
        second=await self.service().poll(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(second.status,"importing")
        valid=lambda subject:AiMetadataAnalysisResult(
            metadata={"subject":subject},provider="fake",model="fake-1",
            provider_request_id=f"req-{subject}",
            usage={"input_tokens":2,"output_tokens":3,"media_units":1})
        self.provider.results=[
            AiBatchResult(items[1].custom_item_id,result=valid("dog")),
            AiBatchResult("unknown-id",result=valid("unknown")),
            AiBatchResult(items[0].custom_item_id,result=valid("cat")),
            AiBatchResult(items[0].custom_item_id,result=valid("duplicate")),
            AiBatchResult(items[2].custom_item_id,result=AiMetadataAnalysisResult(
                metadata={"wrong":True},provider="fake",model="fake-1")),
        ]
        imported=await self.service().import_results(
            tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.assertEqual(imported.status,"partial_failed")
        self.assertEqual(imported.completed_count,2)
        self.assertEqual(imported.failed_count,1)
        self.assertIn("unknown-id",imported.error_json["unknown_custom_item_ids"])
        self.assertEqual(self.session.scalar(select(func.count()).select_from(
            AiUsageRecordModel)),3)
        self.assertEqual(imported.actual_cost_micros,90)
        self.assertEqual(sum(value.provider_reported_cost_micros or 0 for value in
            self.session.scalars(select(AiUsageRecordModel)).all()),90)
        before=self.session.scalar(select(func.count()).select_from(AiUsageRecordModel))
        again=await self.service().import_results(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(again.status,"partial_failed")
        self.assertEqual(self.session.scalar(select(func.count()).select_from(
            AiUsageRecordModel)),before)
        index_jobs=self.session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type=="asset_index")).all()
        self.assertEqual(len(index_jobs),2)

    async def test_streamed_import_resumes_and_marks_missing(self):
        batch=await self._batch();await self.service().submit(
            tenant_id="tenant-a",batch_id=batch.id);self.session.commit()
        items=AiBatchRepository(self.session).items(batch)
        result=lambda value:AiMetadataAnalysisResult(
            metadata={"subject":value},provider="fake",model="fake-1")
        self.provider.results=[
            AiBatchResult(items[0].custom_item_id,result=result("one")),
            AiBatchResult(items[1].custom_item_id,result=result("two")),
        ]
        self.provider.fail_stream_once=True
        with self.assertRaises(AiProviderError):
            await self.service().import_results(tenant_id="tenant-a",batch_id=batch.id)
        self.session.rollback()
        checkpoint=AiBatchRepository(self.session).get_batch("tenant-a",batch.id)
        self.assertEqual(checkpoint.result_cursor,"0")
        imported=await self.service().import_results(
            tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.assertEqual(imported.completed_count,2)
        self.assertEqual(imported.missing_count,1)
        self.assertEqual(imported.status,"partial_failed")

    async def test_ambiguous_submission_retry_and_cancellation(self):
        batch=await self._batch();self.provider.ambiguous_once=True
        with self.assertRaises(AiProviderError):
            await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.assertEqual(AiBatchRepository(self.session).get_batch(
            "tenant-a",batch.id).status,"ambiguous")
        await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.assertEqual(self.provider.submit_calls,2)
        cancelled=await self.service().cancel(
            tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(cancelled.status,"cancelled")
        self.assertEqual(self.provider.cancel_calls,1)

    async def test_local_size_rejection_releases_budget_reservations(self):
        self.settings.AI_BATCH_MAX_REQUEST_BYTES=1
        batch=await self._batch()
        with self.assertRaises(AiProviderError) as raised:
            await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(raised.exception.code,"batch_request_too_large")
        self.assertEqual(self.provider.submit_calls,0)
        reservations=self.session.scalars(select(AiBudgetReservationModel)).all()
        self.assertTrue(reservations)
        self.assertTrue(all(value.status=="reconciled" and
            value.actual_cost_micros==0 for value in reservations))

    async def test_terminal_provider_failure_reconciles_and_enqueues_retry(self):
        batch=await self._batch()
        await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.provider.statuses=[AiBatchStatus(
            "failed",usage={"costMicros":30},
            error_code="provider_failed",error_message="provider stopped")]
        result=await self.service().poll(tenant_id="tenant-a",batch_id=batch.id)
        self.session.commit()
        self.assertEqual(result.status,"failed")
        self.assertEqual(result.actual_cost_micros,30)
        self.assertTrue(all(item.status=="failed" for item in
            AiBatchRepository(self.session).items(result)))
        retries=self.session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type=="ai_batch_retry_items")).all()
        self.assertEqual(len(retries),1)

    async def test_budget_blocks_before_batch_provider_and_fallback_is_explicit(self):
        self.session.add(TenantAiBudgetPolicyModel(
            tenant_id="tenant-a",enabled=True,daily_limit_micros=1,
            monthly_limit_micros=1,warning_threshold_percent=80,
            hard_stop_threshold_percent=100,currency="USD"))
        self.session.commit()
        batch=await self._batch()
        result=await self.service().submit(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(result.status,"partial_failed")
        self.assertEqual(self.provider.submit_calls,0)
        self.assertTrue(all(item.status=="budget_blocked"
            for item in AiBatchRepository(self.session).items(batch)))
        children=self.service().retry_items(tenant_id="tenant-a",batch_id=batch.id)
        self.assertEqual(len(children),1)
        self.assertFalse(any(job.job_type=="asset_analyze" for job in
            self.session.scalars(select(ProcessingJobModel)).all()))
