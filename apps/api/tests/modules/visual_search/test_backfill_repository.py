from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.visual_search.backfill_model import VisualSearchBackfillRunModel
from app.modules.visual_search.backfill_repository import VisualSearchBackfillRunRepository


def repo():
    engine=create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, class_=Session, expire_on_commit=False)()


def test_run_checkpoint_pause_resume_complete_is_durable():
    engine, session=repo()
    try:
        runs=VisualSearchBackfillRunRepository(session)
        run=runs.create(tenant_id="tenant-a", schema_version="v1", requested_by="admin")
        runs.start(run); runs.checkpoint(run, asset_id="asset-7", counters={"scanned":7,"enqueued":3})
        session.commit(); run_id=run.id
        session.close(); session=Session(engine)
        runs=VisualSearchBackfillRunRepository(session); run=runs.get(tenant_id="tenant-a", run_id=run_id)
        assert run and run.status=="running" and run.checkpoint_asset_id=="asset-7" and run.counters_json=={"scanned":7,"enqueued":3}
        runs.pause(run); assert run.status=="paused" and run.paused_at is not None
        runs.start(run); runs.complete(run, counters={"scanned":9,"enqueued":4})
        assert run.status=="completed" and run.completed_at is not None and run.counters_json["enqueued"]==4
    finally:
        session.close(); engine.dispose()


def test_tenant_isolation_and_terminal_transitions():
    engine, session=repo()
    try:
        runs=VisualSearchBackfillRunRepository(session); run=runs.create(tenant_id="tenant-a", schema_version="v1")
        assert runs.get(tenant_id="tenant-b", run_id=run.id) is None
        runs.start(run); runs.cancel(run)
        assert run.status=="cancelled"
        import pytest
        with pytest.raises(ValueError, match="terminal"): runs.cancel(run)
    finally:
        session.close(); engine.dispose()
