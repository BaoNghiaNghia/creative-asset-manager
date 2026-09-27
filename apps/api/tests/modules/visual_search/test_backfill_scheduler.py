from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.visual_search.backfill_control import VisualSearchBackfillController
from app.modules.visual_search.backfill_repository import VisualSearchBackfillRunRepository
from app.modules.visual_search.backfill_scheduler import VisualSearchBackfillScheduler
from app.modules.visual_search.reconciliation import VisualReconciliationResult
import app.modules.visual_search.backfill_executor as executor_module


def _settings(**overrides):
    values = {
        "PROCESSING_JOBS_ENABLED": True,
        "VISUAL_SEARCH_ENABLED": True,
        "VISUAL_SEARCH_BACKFILL_ENABLED": True,
        "VISUAL_SEARCH_CANARY_TENANT_IDS": "tenant-a",
    }
    values.update(overrides)
    return Settings(**values)


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_scheduler_creates_and_advances_initial_durable_run(monkeypatch):
    engine, sessions = _sessions()
    calls = []

    class Reconcile:
        def __init__(self, *args, **kwargs):
            pass

        def reconcile(self, **kwargs):
            calls.append(kwargs)
            return VisualReconciliationResult(
                scanned=2,
                current=0,
                missing=2,
                stale=0,
                enqueued=2,
                existing=0,
                checkpoint_asset_id="asset-b",
                has_more=False,
            )

    monkeypatch.setattr(executor_module, "VisualSearchReconciliationService", Reconcile)
    try:
        scheduler = VisualSearchBackfillScheduler(sessions, _settings(), object())
        result = scheduler.tick()
        assert len(result) == 1
        assert result[0].status == "completed"
        assert result[0].enqueued == 2
        assert len(calls) == 1

        with sessions() as session:
            latest = VisualSearchBackfillRunRepository(session).latest(
                tenant_id="tenant-a"
            )
            assert latest is not None
            assert latest.status == "completed"
            assert latest.counters_json["scanned"] == 2

        # Completion is durable. Later scheduler ticks must not create an
        # endless full-corpus reconciliation loop for the same schema.
        assert scheduler.tick()[0].status == "completed"
        assert len(calls) == 1
    finally:
        engine.dispose()


def test_scheduler_respects_operator_pause(monkeypatch):
    engine, sessions = _sessions()
    try:
        with sessions() as session:
            run = VisualSearchBackfillController(session).start_or_resume(
                tenant_id="tenant-a"
            )
            VisualSearchBackfillRunRepository(session).pause(run)
            session.commit()

        class UnexpectedReconcile:
            def __init__(self, *args, **kwargs):
                raise AssertionError("paused run must not execute")

        monkeypatch.setattr(
            executor_module,
            "VisualSearchReconciliationService",
            UnexpectedReconcile,
        )
        scheduler = VisualSearchBackfillScheduler(sessions, _settings(), object())
        result = scheduler.tick()
        assert result[0].status == "paused"
    finally:
        engine.dispose()


def test_scheduler_is_fail_closed_when_backfill_flag_is_disabled():
    engine, sessions = _sessions()
    try:
        scheduler = VisualSearchBackfillScheduler(
            sessions,
            _settings(VISUAL_SEARCH_BACKFILL_ENABLED=False),
            object(),
        )
        assert scheduler.enabled is False
        assert scheduler.tick() == ()
        with Session(engine) as session:
            assert (
                VisualSearchBackfillRunRepository(session).latest(
                    tenant_id="tenant-a"
                )
                is None
            )
    finally:
        engine.dispose()
