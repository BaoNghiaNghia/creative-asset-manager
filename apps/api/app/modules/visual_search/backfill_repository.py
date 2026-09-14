from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.visual_search.backfill_model import VisualSearchBackfillRunModel


def now() -> datetime: return datetime.now(timezone.utc)


class VisualSearchBackfillRunRepository:
    def __init__(self, session: Session): self.session = session
    def create(self, *, tenant_id: str, schema_version: str, requested_by: str | None = None) -> VisualSearchBackfillRunModel:
        row=VisualSearchBackfillRunModel(tenant_id=tenant_id, schema_version=schema_version, requested_by=requested_by, counters_json={})
        self.session.add(row); self.session.flush(); return row
    def get(self, *, tenant_id: str, run_id: str) -> VisualSearchBackfillRunModel | None:
        row=self.session.get(VisualSearchBackfillRunModel, run_id)
        return row if row is not None and row.tenant_id == tenant_id else None
    def active(self, *, tenant_id: str) -> VisualSearchBackfillRunModel | None:
        return self.session.scalar(select(VisualSearchBackfillRunModel).where(VisualSearchBackfillRunModel.tenant_id==tenant_id, VisualSearchBackfillRunModel.status.in_(("pending","running","paused"))).order_by(VisualSearchBackfillRunModel.created_at.desc()).limit(1))
    def start(self, row: VisualSearchBackfillRunModel) -> None:
        if row.status not in {'pending','paused'}: raise ValueError('backfill run is not resumable')
        row.status='running'; row.paused_at=None; row.error_code=None; row.error_message=None; row.updated_at=now(); self.session.flush()
    def pause(self, row: VisualSearchBackfillRunModel) -> None:
        if row.status != 'running': raise ValueError('backfill run is not running')
        row.status='paused'; row.paused_at=now(); row.updated_at=now(); self.session.flush()
    def cancel(self, row: VisualSearchBackfillRunModel) -> None:
        if row.status in {'completed','failed','cancelled'}: raise ValueError('backfill run is terminal')
        row.status='cancelled'; row.completed_at=now(); row.updated_at=now(); self.session.flush()
    def checkpoint(self, row: VisualSearchBackfillRunModel, *, asset_id: str | None, counters: dict) -> None:
        row.checkpoint_asset_id=asset_id; row.counters_json=dict(counters); row.updated_at=now(); self.session.flush()
    def complete(self, row: VisualSearchBackfillRunModel, *, counters: dict) -> None:
        row.status='completed'; row.counters_json=dict(counters); row.completed_at=now(); row.updated_at=now(); self.session.flush()
