from datetime import datetime,timezone
from fastapi import APIRouter,Depends
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal,require_permission
from app.modules.visual_search.coverage import VisualCoverageService
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.core.config import get_settings
router=APIRouter(prefix="/api/v1/admin/visual-search",tags=["visual-search-admin"]); READ=require_permission("ai_operations.read")
def service():
 s=get_settings();return VisualCoverageService(SessionLocal,VisualSearchElasticsearchIndex(ElasticsearchV3Config(s.ELASTICSEARCH_URL,s.ELASTICSEARCH_INDEX_PREFIX,index_generation="v3"),__import__("app.modules.visual_search.model_spec",fromlist=["VISUAL_SEARCH_BASELINE_DESCRIPTOR"]).VISUAL_SEARCH_BASELINE_DESCRIPTOR))
@router.get("/coverage")
def coverage(principal:CurrentPrincipal=Depends(READ)):
 r=service().collect(principal.active_tenant_id);return {"generated_at":datetime.now(timezone.utc),"index_state":r.index_state,"totals":r.totals,"ratios":r.ratios}
@router.get("/coverage/sources")
def sources(principal:CurrentPrincipal=Depends(READ)):
 r=service().collect(principal.active_tenant_id);return {"index_state":r.index_state,"sources":r.sources}
