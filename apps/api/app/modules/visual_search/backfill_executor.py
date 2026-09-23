from __future__ import annotations
from app.modules.visual_search.backfill_repository import VisualSearchBackfillRunRepository
from app.modules.visual_search.reconciliation import VisualSearchReconciliationService
from app.modules.visual_search.eligibility import visual_search_tenant_eligible

class VisualSearchBackfillExecutor:
    """Executes exactly one bounded reconciliation slice and persists its cursor."""
    def __init__(self, session, processing, index, *, settings):
        self.session=session; self.processing=processing; self.index=index; self.settings=settings
    def run_slice(self, *, tenant_id: str, run_id: str, max_assets: int=100):
        if not self.settings.VISUAL_SEARCH_BACKFILL_ENABLED or not visual_search_tenant_eligible(self.settings, tenant_id): raise ValueError("visual search backfill is disabled")
        runs=VisualSearchBackfillRunRepository(self.session); run=runs.get(tenant_id=tenant_id,run_id=run_id)
        if run is None: raise LookupError('visual backfill run not found')
        if run.status=='cancelled': return run, None
        if run.status in {'pending','paused'}: runs.start(run)
        if run.status!='running': raise ValueError('visual backfill run is not runnable')
        result=VisualSearchReconciliationService(self.session,self.processing,self.index,settings=self.settings).reconcile(tenant_id=tenant_id,max_assets=max_assets,after_asset_id=run.checkpoint_asset_id)
        counters=dict(run.counters_json or {})
        for key in ('scanned','current','missing','stale','enqueued','existing'):
            counters[key]=int(counters.get(key,0))+int(getattr(result,key))
        counters["throttled_slices"] = int(counters.get("throttled_slices", 0)) + int(result.throttled)
        counters["last_queue_depth"] = int(result.queue_depth)
        counters["last_queue_capacity"] = int(result.queue_capacity)
        if result.has_more: runs.checkpoint(run,asset_id=result.checkpoint_asset_id,counters=counters)
        else: runs.complete(run,counters=counters)
        return run,result