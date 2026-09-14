from __future__ import annotations

from app.modules.visual_search.backfill_repository import VisualSearchBackfillRunRepository
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


class VisualSearchBackfillController:
    """Tenant-bound state control; execution is deliberately performed separately."""
    def __init__(self, session): self.runs=VisualSearchBackfillRunRepository(session)
    def start_or_resume(self, *, tenant_id: str, actor_id: str | None = None):
        run=self.runs.active(tenant_id=tenant_id)
        if run is None:
            run=self.runs.create(tenant_id=tenant_id, schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION, requested_by=actor_id)
        if run.status in {'pending','paused'}: self.runs.start(run)
        return run
    def pause(self, *, tenant_id: str, run_id: str):
        run=self.runs.get(tenant_id=tenant_id, run_id=run_id)
        if run is None: raise LookupError('visual backfill run not found')
        if run.status=='paused': return run
        self.runs.pause(run); return run
    def cancel(self, *, tenant_id: str, run_id: str):
        run=self.runs.get(tenant_id=tenant_id, run_id=run_id)
        if run is None: raise LookupError('visual backfill run not found')
        if run.status=='cancelled': return run
        self.runs.cancel(run); return run
