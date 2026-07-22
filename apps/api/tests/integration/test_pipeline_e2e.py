from __future__ import annotations
import asyncio, io, os, threading, unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.domain.processing.handlers import WorkerDependencies
from app.domain.providers.registry import AiProviderRegistry
from app.domain.providers.contracts import AiMetadataAnalysisResult, AssetDownloadStream, StoredAsset, StoredAssetReadStream
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config, ElasticsearchV2Index
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.ai_governance.model import AiCostRateModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel, AssetSourceLinkModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.external_ingestion.model import AssetIngestionItemModel, AssetIngestionModel
from app.modules.external_ingestion.repository import ExternalIngestionRepository
from app.modules.external_ingestion.router import router as ingestion_router
from app.modules.pipeline.handlers import AssetIndexJobHandler, AssetStoreJobHandler, SearchProjectionBuildJobHandler, SourceAssetDownloadJobHandler
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.pipeline.stages import ProviderDownloadStage, ProviderStorageStage
from app.modules.pipeline.state import PipelineState
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.registry import build_handler_registry
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.modules.processing.service import ProcessingJobService
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.search.query_builder import ElasticsearchQueryBuilder
from app.modules.search.query_parser import SearchQueryParser
from app.modules.storage.model import AssetStorageObjectModel

DATABASE_URL=os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL","")
ELASTICSEARCH_URL=os.getenv("INTEGRATION_ELASTICSEARCH_URL") or os.getenv("ELASTICSEARCH_URL","")
SERVICES_AVAILABLE=DATABASE_URL.startswith(("postgresql://","postgresql+psycopg://")) and ELASTICSEARCH_URL.startswith("http")

def png_bytes(color="red"):
    output=io.BytesIO(); Image.new("RGB",(12,8),color).save(output,format="PNG"); return output.getvalue()
async def no_op_close(): return None

class FakePipelineResolver:
    def __init__(self,session_factory,contents,failures=0):
        self.session_factory=session_factory; self.contents=contents; self.failures=failures; self.calls=0; self._lock=threading.Lock()
    @asynccontextmanager
    async def open(self,*,tenant_id,pipeline):
        with self.session_factory() as session:
            item=session.get(AssetIngestionItemModel,pipeline.origin_id)
            if item is None or item.tenant_id!=tenant_id: raise LookupError(pipeline.origin_id)
            ingestion=session.get(AssetIngestionModel,item.ingestion_id)
            source_asset=AssetRegistryRepository(session).upsert_source_asset(
                tenant_id=tenant_id,external_source_id=ingestion.external_source_id,
                external_asset_id=item.external_asset_id,filename=item.filename,mime_type="image/png",
                size_bytes=len(self.contents[item.external_asset_id]),provider_checksum=item.provider_checksum,
                source_metadata={"path":"integration/incoming"})
            item.source_asset_id=source_asset.id; session.commit(); pipeline.source_asset_id=source_asset.id
        with self._lock:
            self.calls+=1
            if self.failures:
                self.failures-=1; raise RuntimeError("temporary fake download failure")
        content=self.contents[item.external_asset_id]
        async def body():
            midpoint=max(1,len(content)//2); yield content[:midpoint]; yield content[midpoint:]
        yield AssetDownloadStream(body=body(),close=no_op_close,content_type="image/png")

class FakeManagedStorage:
    provider_name="google_drive_managed"
    def __init__(self): self.content_by_remote_id={}; self.store_calls=0
    async def store_asset(self,input):
        payload=bytearray()
        async for chunk in input.body: payload.extend(chunk)
        remote_id=f"fake-{input.asset_id}"; self.content_by_remote_id[remote_id]=bytes(payload); self.store_calls+=1
        return StoredAsset(storage_key=f"fake://{remote_id}",content_hash=input.content_hash,size_bytes=len(payload),storage_provider=self.provider_name,remote_file_id=remote_id,remote_folder_id="fake-folder",web_url=None)
    async def open_asset(self,input):
        content=self.content_by_remote_id[input.remote_file_id]
        async def body(): yield content
        return StoredAssetReadStream(body=body(),close=no_op_close,content_type=input.content_type or "image/png",size_bytes=len(content))
    async def store_metadata_sidecar(self,_input): raise AssertionError("sidecar disabled")

class FakeGemini:
    provider_name="gemini"; model="fake-gemini-v1"; supports_batch=False
    def __init__(self,metadata): self.metadata=metadata; self.calls=0
    async def analyze_single(self,_input):
        self.calls+=1
        return AiMetadataAnalysisResult(metadata=self.metadata,provider=self.provider_name,model=self.model,provider_request_id=f"fake-request-{self.calls}",usage={"inputTokens":10,"outputTokens":10,"mediaUnits":1},provider_metadata={"finish_reason":"STOP"})

class LoopSafeSearchProvider:
    def __init__(self,base_url,prefix,failures=0):
        self.config=ElasticsearchV2Config(base_url,index_prefix=prefix); self.failures=failures; self._lock=threading.Lock()
    async def initialize(self):
        async with ElasticsearchV2Index(self.config) as index:
            physical=await index.create_index("000001"); await index.switch_aliases(physical)
    async def bulk_upsert(self,documents):
        with self._lock:
            if self.failures:
                self.failures-=1; raise RuntimeError("temporary fake Elasticsearch transport failure")
        async with ElasticsearchV2Index(self.config) as index: return await index.bulk_upsert(documents)
    async def search(self,body):
        async with ElasticsearchV2Index(self.config) as index:
            await index._request("POST",f"/{index.write_alias}/_refresh"); return await index.search(body)
    async def cleanup(self):
        async with ElasticsearchV2Index(self.config) as index:
            await index.client.delete(f"/{self.config.index_prefix}-v2-*",params={"expand_wildcards":"all"})

@unittest.skipUnless(SERVICES_AVAILABLE,"PostgreSQL and Elasticsearch integration services are not configured")
class DurablePipelineEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=create_engine(DATABASE_URL,pool_pre_ping=True)
        cls.sessions=sessionmaker(cls.engine,class_=Session,expire_on_commit=False)
    @classmethod
    def tearDownClass(cls): cls.engine.dispose()
    def setUp(self):
        self.marker=uuid4().hex[:12]; self.tenant_id=f"e2e-{self.marker}"; self.token=f"e2e-token-{uuid4().hex}"; self.prefix=f"cam-e2e-{self.marker}"
        self.settings=Settings(PROCESSING_JOBS_ENABLED=True,UNIFIED_ASSET_INGESTION_ENABLED=True,CONTENT_DEDUP_ENABLED=True,MANAGED_ASSET_STORAGE_ENABLED=True,DYNAMIC_AI_METADATA_ENABLED=True,AI_SINGLE_ANALYSIS_ENABLED=True,AI_AUTO_ANALYZE_ENABLED=True,SEARCH_PROJECTION_ENABLED=True,ELASTICSEARCH_V2_ENABLED=True,SEARCH_QUERY_PARSER_V2_ENABLED=True,EXTERNAL_INGESTION_API_ENABLED=True,SENSITIVE_URL_ENCRYPTION_KEYS="v1:eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",GEMINI_API_KEY="fake-no-network-key",GEMINI_MODEL="fake-gemini-v1",GEMINI_ALLOWED_MODELS="fake-gemini-v1",WORKER_LEASE_SECONDS=10,WORKER_HEARTBEAT_SECONDS=.2,WORKER_IDLE_POLL_SECONDS=.01)
        with self.sessions() as session:
            source=AssetRegistryRepository(session).upsert_external_source(tenant_id=self.tenant_id,source_key=f"external-{self.marker}",source_type="external_api")
            ExternalIngestionRepository(session).create_credential(tenant_id=self.tenant_id,external_source_id=source.id,name="Step 31 fake source",raw_key=self.token,rate_limit_per_minute=100)
            AiMetadataRepository(session).create_profile(tenant_id=self.tenant_id,profile_name="integration",profile_version="1",prompt_template="Describe {{ asset }}",optional_json_schema={"type":"object","properties":{"subject":{"type":"string"},"label":{"type":"string"},"year":{"type":"integer"}},"required":["subject"]},search_config={"include_all_scalar_values":True,"facet_paths":{"subject":["subject"]}})
            session.add(TenantProcessingPolicyModel(tenant_id=self.tenant_id,pipeline_enabled=True,source_sync_enabled=True,download_enabled=True,managed_storage_enabled=True,ai_analysis_enabled=True,search_v2_enabled=True,sidecar_enabled=False,total_active_jobs_limit=4,ai_active_jobs_limit=1,source_active_jobs_limit=2,storage_active_jobs_limit=2))
            session.add(AiCostRateModel(provider="gemini",model="fake-gemini-v1",processing_mode="single",effective_at=datetime.now(timezone.utc)-timedelta(seconds=1),input_unit_cost=0,output_unit_cost=0,media_unit_cost=0))
            session.commit(); self.source_id=source.id
    def tearDown(self):
        if getattr(self,"search",None) is not None: asyncio.run(self.search.cleanup())
    def submit(self,external_ids):
        app=FastAPI(); app.include_router(ingestion_router)
        def database_override():
            with self.sessions() as session: yield session
        app.dependency_overrides[get_db]=database_override; app.dependency_overrides[get_settings]=lambda:self.settings
        with TestClient(app) as client, patch("app.modules.external_ingestion.router.get_settings",return_value=self.settings):
            response=client.post("/api/v1/asset-ingestions",headers={"Authorization":f"Bearer {self.token}","Idempotency-Key":f"e2e-{self.marker}"},json={"source_id":self.source_id,"items":[{"external_asset_id":item,"download_url":f"https://fake.invalid/{item}.png","filename":f"{item}.png"} for item in external_ids]})
        self.assertEqual(response.status_code,202,response.text)
        with self.sessions() as session:
            pipelines=list(session.scalars(select(AssetPipelineModel).where(AssetPipelineModel.tenant_id==self.tenant_id).order_by(AssetPipelineModel.created_at)))
        return [pipeline.id for pipeline in pipelines]
    def runtime(self,contents,download_failures=0,invalid_metadata=False,search_failures=0):
        resolver=FakePipelineResolver(self.sessions,contents,download_failures); storage=FakeManagedStorage()
        ai=FakeGemini({"wrong":True} if invalid_metadata else {"subject":"cat","label":"mama est 2015","year":2015})
        self.search=LoopSafeSearchProvider(ELASTICSEARCH_URL,self.prefix,search_failures); asyncio.run(self.search.initialize())
        ai_registry=AiProviderRegistry();ai_registry.register(ai.provider_name,ai)
        dependencies=WorkerDependencies(session_factory=self.sessions,storage_provider=storage,ai_provider_registry=ai_registry,resources={"pipeline_download_stage":ProviderDownloadStage(self.sessions,resolver),"pipeline_storage_stage":ProviderStorageStage(self.sessions,resolver,storage),"search_index_provider":self.search})
        handlers=build_handler_registry((("source_asset_download",SourceAssetDownloadJobHandler(self.settings)),("asset_store",AssetStoreJobHandler(self.settings)),("asset_analyze",AssetAnalyzeJobHandler(self.settings)),("search_projection_build",SearchProjectionBuildJobHandler(self.settings)),("asset_index",AssetIndexJobHandler(self.settings))))
        runtime=WorkerRuntime(config=WorkerRuntimeConfig(worker_id=f"e2e-worker-{self.marker}",enabled=True,lease_seconds=10,heartbeat_seconds=.2,idle_poll_seconds=.01,enforce_tenant_policy=True,allowed_job_types=("source_asset_download","asset_store","asset_analyze","search_projection_build","asset_index")),dependencies=dependencies,registry=handlers)
        return runtime,resolver,storage,ai
    def drain(self,runtime,force_retries=False):
        processed=0
        for _ in range(30):
            if runtime.run_once(): processed+=1; continue
            if force_retries:
                with self.sessions() as session:
                    changed=session.execute(update(ProcessingJobModel).where(ProcessingJobModel.tenant_id==self.tenant_id,ProcessingJobModel.status=="retry").values(next_attempt_at=datetime.now(timezone.utc))).rowcount; session.commit()
                if changed: continue
            break
        return processed
    def assert_searchable(self,asset_id):
        body=ElasticsearchQueryBuilder().build(SearchQueryParser().parse("cat, mama, 2015"),tenant_id=self.tenant_id)
        result=asyncio.run(self.search.search(body))
        self.assertEqual({hit["_id"] for hit in result["hits"]["hits"]},{asset_id})
    def pipeline(self,pipeline_id):
        with self.sessions() as session: return session.get(AssetPipelineModel,pipeline_id)
    def test_ingestion_reaches_searchable_completed_asset(self):
        ids=self.submit(["cat-happy"]); runtime,resolver,storage,ai=self.runtime({"cat-happy":png_bytes()})
        try: self.assertGreaterEqual(self.drain(runtime,True),4)
        finally: runtime.close()
        with self.sessions() as session:
            pipeline=session.get(AssetPipelineModel,ids[0]); analysis=session.get(AssetAiAnalysisModel,pipeline.analysis_id)
            stored=session.scalar(select(AssetStorageObjectModel).where(AssetStorageObjectModel.tenant_id==self.tenant_id,AssetStorageObjectModel.asset_id==pipeline.asset_id))
            jobs=list(session.scalars(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id==self.tenant_id)))
            self.assertEqual(pipeline.state,PipelineState.COMPLETED.value); self.assertEqual(analysis.status,"completed"); self.assertEqual(stored.status,"stored"); self.assertTrue(all(job.status=="completed" for job in jobs), [(job.job_type, job.status, job.last_error_code, job.last_error_message) for job in jobs]); asset_id=pipeline.asset_id
        self.assertEqual(ai.calls,1); self.assertGreaterEqual(resolver.calls,2); self.assertEqual(storage.store_calls,1); self.assert_searchable(asset_id)
    def test_transient_download_and_elasticsearch_failures_retry(self):
        ids=self.submit(["cat-retry"]); runtime,*_=self.runtime({"cat-retry":png_bytes("blue")},download_failures=1,search_failures=1)
        try: self.assertGreaterEqual(self.drain(runtime,True),6)
        finally: runtime.close()
        with self.sessions() as session:
            pipeline=session.get(AssetPipelineModel,ids[0]); jobs=list(session.scalars(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id==self.tenant_id)))
            self.assertEqual(pipeline.state,PipelineState.COMPLETED.value); self.assertGreaterEqual(max(job.attempt_count for job in jobs),2); asset_id=pipeline.asset_id
        self.assert_searchable(asset_id)
    def test_duplicate_content_reuses_asset_and_analysis(self):
        ids=self.submit(["same-a","same-b"]); content=png_bytes("green"); runtime,_,storage,ai=self.runtime({"same-a":content,"same-b":content})
        try: self.assertGreaterEqual(self.drain(runtime),7)
        finally: runtime.close()
        with self.sessions() as session:
            pipelines=[session.get(AssetPipelineModel,item) for item in ids]
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetModel).where(AssetModel.tenant_id==self.tenant_id)),1)
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetSourceLinkModel).where(AssetSourceLinkModel.tenant_id==self.tenant_id)),2)
            self.assertEqual(pipelines[0].asset_id,pipelines[1].asset_id); self.assertTrue(all(item.state==PipelineState.COMPLETED.value for item in pipelines))
        self.assertEqual(ai.calls,1); self.assertEqual(storage.store_calls,1)
    def test_invalid_ai_metadata_stops_before_indexing(self):
        ids=self.submit(["cat-invalid"]); runtime,_,_,ai=self.runtime({"cat-invalid":png_bytes("yellow")},invalid_metadata=True)
        try: self.assertGreaterEqual(self.drain(runtime),3)
        finally: runtime.close()
        with self.sessions() as session:
            pipeline=session.get(AssetPipelineModel,ids[0]); analysis=session.get(AssetAiAnalysisModel,pipeline.analysis_id)
            index_jobs=session.scalar(select(func.count()).select_from(ProcessingJobModel).where(ProcessingJobModel.tenant_id==self.tenant_id,ProcessingJobModel.job_type=="asset_index"))
            self.assertEqual(analysis.status,"pending"); self.assertTrue(analysis.validation_errors_json); self.assertNotEqual(pipeline.state,PipelineState.COMPLETED.value); self.assertEqual(index_jobs,0)
        self.assertGreaterEqual(ai.calls,1)
    def test_worker_restart_recovers_expired_lease(self):
        ids=self.submit(["cat-restart"]); now=datetime.now(timezone.utc)
        with self.sessions() as session: claimed=ProcessingJobService(ProcessingRepository(session)).claim_next(worker_id="crashed-worker",lease_seconds=1,now=now,enforce_tenant_policy=True,allowed_job_types=("source_asset_download",))
        with self.sessions() as session:
            session.execute(update(ProcessingJobModel).where(ProcessingJobModel.id==claimed.id).values(lease_expires_at=now-timedelta(seconds=1))); session.commit()
        runtime,*_=self.runtime({"cat-restart":png_bytes("purple")})
        try: self.assertGreaterEqual(self.drain(runtime),4)
        finally: runtime.close()
        with self.sessions() as session:
            pipeline=session.get(AssetPipelineModel,ids[0]); recovered=session.get(ProcessingJobModel,claimed.id)
            self.assertEqual(pipeline.state,PipelineState.COMPLETED.value); self.assertEqual(recovered.attempt_count,2)
    def test_disabled_tenant_is_not_claimed_then_can_resume(self):
        ids=self.submit(["cat-paused"])
        with self.sessions() as session:
            session.get(TenantProcessingPolicyModel,self.tenant_id).pipeline_enabled=False; session.commit()
        runtime,*_=self.runtime({"cat-paused":png_bytes("orange")})
        try:
            self.assertFalse(runtime.run_once())
            with self.sessions() as session:
                self.assertEqual(session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id==self.tenant_id)).status,"pending")
                session.get(TenantProcessingPolicyModel,self.tenant_id).pipeline_enabled=True; session.commit()
            self.assertGreaterEqual(self.drain(runtime),4)
        finally: runtime.close()
        self.assertEqual(self.pipeline(ids[0]).state,PipelineState.COMPLETED.value)

if __name__=="__main__": unittest.main()
