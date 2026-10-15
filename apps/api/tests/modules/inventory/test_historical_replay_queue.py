from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import InventorySettingsModel
from app.modules.inventory.daily_sheet.replay_queue import HistoricalReplayQueue


def _sessions():
    tmp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(tmp.name) / 'queue.db'}")
    event.listen(engine, "connect", lambda con, _: con.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants", "oauth_connections", "external_sources", "inventory_settings", "inventory_jobs"):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
        session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive"))
        session.add(InventorySettingsModel(
            tenant_id="tenant-a", enabled=True, image_pipeline_enabled=False,
            daily_sheet_automation_enabled=True, external_source_id="source-a", inbox_folder_id="x",
            daily_sheet_config_json={"version": 4},
        ))
    return tmp, engine, sessions


def test_historical_replay_queue_deduplicates_and_completes_without_live_workbook_mutation():
    tmp, engine, sessions = _sessions()
    try:
        calls = []
        class Service:
            def replay_agent_v4_historical(self, tenant, business_date, *, mode, promote):
                calls.append((tenant, business_date, mode, promote))
                return {"status": "completed", "verification_status": "verified", "promoted": promote, "run_id": "verified-run"}

        queue = HistoricalReplayQueue(sessions, service_factory=lambda: Service(), recovery=lambda *_: {"status": "outside_safe_window"})
        day = date(2030, 8, 9)
        one = queue.enqueue("tenant-a", day, "fresh_copy", True)
        two = queue.enqueue("tenant-a", day, "fresh_copy", True)
        assert one["job_id"] == two["job_id"]
        assert one["status"] == "pending"
        try:
            queue.enqueue("tenant-a", day, "fresh_copy", False)
            assert False, "sandbox and promotion requests cannot silently share one job"
        except ValueError as exc:
            assert str(exc) == "inventory_historical_replay_conflicting_active_request"
        assert [item["job_id"] for item in queue.active_jobs("tenant-a")] == [one["job_id"]]
        assert queue.active_jobs("other-tenant") == []
        assert queue.run_once(frozenset({"other-tenant"})) == 0
        assert queue.run_once(frozenset({"tenant-a"})) == 1
        result = queue.status("tenant-a", one["job_id"])
        assert result["status"] == "completed"
        assert result["result"]["promoted"] is True
        assert calls == [("tenant-a", day, "fresh_copy", True)]
        assert queue.enqueue("tenant-a", day, "fresh_copy", True)["job_id"] != one["job_id"]
    finally:
        engine.dispose()
        tmp.cleanup()


def test_historical_replay_queue_retries_provider_failure_without_duplicate_dispatch():
    tmp, engine, sessions = _sessions()
    try:
        from app.modules.inventory.ai.gateway import InventoryAiGatewayError
        calls = []
        class Service:
            def replay_agent_v4_historical(self, *_args, **_kwargs):
                calls.append(1)
                raise InventoryAiGatewayError("inventory_gemini_rate_limited", retryable=True)
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: Service())
        job = queue.enqueue("tenant-a", date(2030, 8, 9), "fresh_copy", True)
        assert queue.run_once(frozenset({"tenant-a"})) == 0
        state = queue.status("tenant-a", job["job_id"])
        assert state["status"] == "retry"
        assert state["attempt_count"] == 1
        assert queue.run_once(frozenset({"tenant-a"})) == 0
        assert len(calls) == 1
        with sessions() as session:
            stored = session.get(InventoryJobModel, job["job_id"])
            assert stored.next_attempt_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    finally:
        engine.dispose()
        tmp.cleanup()


def test_historical_replay_queue_rejects_non_auto_tenant_and_blocks_cross_tenant_job_reads():
    tmp, engine, sessions = _sessions()
    try:
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        job = queue.enqueue("tenant-a", date(2030, 8, 9), "fresh_copy", False)
        try:
            queue.status("tenant-b", job["job_id"])
            assert False, "expected inaccessible job"
        except LookupError:
            pass
        with sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == "tenant-a"))
            settings.daily_sheet_automation_enabled = False
        try:
            queue.enqueue("tenant-a", date(2030, 8, 10), "fresh_copy", True)
            assert False, "expected disabled"
        except ValueError:
            pass
    finally:
        engine.dispose()
        tmp.cleanup()
