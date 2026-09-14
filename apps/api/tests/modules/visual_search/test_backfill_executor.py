from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.core.config import Settings
from app.modules.visual_search.backfill_control import VisualSearchBackfillController
from app.modules.visual_search.backfill_executor import VisualSearchBackfillExecutor
from app.modules.visual_search.reconciliation import VisualReconciliationResult
import app.modules.visual_search.backfill_executor as executor_module


def test_executor_persists_cursor_and_completes_without_duplicate_run(monkeypatch):
 engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool);Base.metadata.create_all(engine);session=Session(engine)
 try:
  run=VisualSearchBackfillController(session).start_or_resume(tenant_id="tenant-a")
  results=[VisualReconciliationResult(2,0,2,0,2,0,"b",True),VisualReconciliationResult(1,1,0,0,0,0,"c",False)]
  class Reconcile:
   def __init__(self,*args,**kwargs): pass
   def reconcile(self,**kwargs): return results.pop(0)
  monkeypatch.setattr(executor_module,"VisualSearchReconciliationService",Reconcile)
  runner=VisualSearchBackfillExecutor(session,object(),object(),settings=Settings(VISUAL_SEARCH_ENABLED=True,VISUAL_SEARCH_BACKFILL_ENABLED=True,VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a"))
  saved,first=runner.run_slice(tenant_id="tenant-a",run_id=run.id,max_assets=2)
  assert saved.status=="running" and saved.checkpoint_asset_id=="b" and saved.counters_json["scanned"]==2
  saved,second=runner.run_slice(tenant_id="tenant-a",run_id=run.id,max_assets=2)
  assert saved.status=="completed" and saved.counters_json["scanned"]==3 and saved.counters_json["enqueued"]==2
 finally: session.close();engine.dispose()
