from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal,require_permission
from app.modules.visual_search.coverage import VisualCoverageService
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.core.config import get_settings
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.backfill_control import VisualSearchBackfillController
from app.modules.visual_search.backfill_executor import VisualSearchBackfillExecutor
router=APIRouter(prefix="/api/v1/admin/visual-search",tags=["visual-search-admin"]); READ=require_permission("ai_operations.read"); WRITE=require_permission("ai_jobs.retry")
class SliceRequest(BaseModel): max_assets:int=100
def service():
 s=get_settings();return VisualCoverageService(SessionLocal,VisualSearchElasticsearchIndex(ElasticsearchV3Config(s.ELASTICSEARCH_URL,s.ELASTICSEARCH_INDEX_PREFIX,index_generation="v3"),__import__("app.modules.visual_search.model_spec",fromlist=["VISUAL_SEARCH_BASELINE_DESCRIPTOR"]).VISUAL_SEARCH_BASELINE_DESCRIPTOR))
@router.get("/coverage")
def coverage(principal:CurrentPrincipal=Depends(READ)):
 r=service().collect(principal.active_tenant_id);return {"generated_at":datetime.now(timezone.utc),"index_state":r.index_state,"totals":r.totals,"ratios":r.ratios}
@router.get("/coverage/sources")
def sources(principal:CurrentPrincipal=Depends(READ)):
 r=service().collect(principal.active_tenant_id);return {"index_state":r.index_state,"sources":r.sources}
@router.get("/coverage/dashboard")
def coverage_dashboard(principal:CurrentPrincipal=Depends(READ)):
 r=service().collect(principal.active_tenant_id);return {"generated_at":datetime.now(timezone.utc),"index_state":r.index_state,"totals":r.totals,"ratios":r.ratios,"sources":r.sources}

def _run(tenant_id, principal):
 with SessionLocal() as session:
  control=VisualSearchBackfillController(session); run=control.start_or_resume(tenant_id=tenant_id,actor_id=principal.actor_id); session.commit(); return run
@router.post("/backfills")
def start_backfill(principal:CurrentPrincipal=Depends(WRITE)):
 run=_run(principal.active_tenant_id,principal); return {"id":run.id,"status":run.status,"checkpoint_asset_id":run.checkpoint_asset_id}
@router.post("/backfills/{run_id}/pause")
def pause_backfill(run_id:str,principal:CurrentPrincipal=Depends(WRITE)):
 with SessionLocal() as session:
  try: run=VisualSearchBackfillController(session).pause(tenant_id=principal.active_tenant_id,run_id=run_id)
  except LookupError: raise HTTPException(404,"visual backfill run not found")
  session.commit(); return {"id":run.id,"status":run.status}
@router.post("/backfills/{run_id}/cancel")
def cancel_backfill(run_id:str,principal:CurrentPrincipal=Depends(WRITE)):
 with SessionLocal() as session:
  try: run=VisualSearchBackfillController(session).cancel(tenant_id=principal.active_tenant_id,run_id=run_id)
  except LookupError: raise HTTPException(404,"visual backfill run not found")
  session.commit(); return {"id":run.id,"status":run.status}
@router.post("/backfills/{run_id}/run")
def run_backfill_slice(run_id:str,body:SliceRequest,principal:CurrentPrincipal=Depends(WRITE)):
 settings=get_settings()
 with SessionLocal() as session:
  index=VisualSearchElasticsearchIndex(ElasticsearchV3Config(settings.ELASTICSEARCH_URL,settings.ELASTICSEARCH_INDEX_PREFIX,index_generation="v3"),__import__("app.modules.visual_search.model_spec",fromlist=["VISUAL_SEARCH_BASELINE_DESCRIPTOR"]).VISUAL_SEARCH_BASELINE_DESCRIPTOR)
  try: run,result=VisualSearchBackfillExecutor(session,ProcessingRepository(session),index,settings=settings).run_slice(tenant_id=principal.active_tenant_id,run_id=run_id,max_assets=body.max_assets)
  except LookupError: raise HTTPException(404,"visual backfill run not found")
  except ValueError as exc: raise HTTPException(409,str(exc))
  session.commit(); return {"id":run.id,"status":run.status,"checkpoint_asset_id":run.checkpoint_asset_id,"result":result.__dict__ if result else None}
