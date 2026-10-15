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
from app.modules.inventory.persistence_model import InventorySettingsModel, InventoryDailySheetSnapshotModel
from app.modules.inventory.daily_sheet.replay_queue import HistoricalReplayQueue


def _sessions():
    tmp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(tmp.name) / 'queue.db'}")
    event.listen(engine, "connect", lambda con, _: con.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants", "oauth_connections", "external_sources", "inventory_settings", "inventory_jobs", "inventory_daily_sheet_snapshots"):
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
        outcomes = queue.recent_replay_results(
            frozenset({"tenant-a"}), now=datetime.now(timezone.utc)
        )
        assert outcomes[0]["business_date"] == "2030-08-09"
        assert outcomes[0]["promoted"] is True
        assert queue.recent_replay_results(frozenset({"other-tenant"})) == []
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



def _failed_evening(sessions, day, *, code="inventory_gemini_request_failed", status="failed"):
    with sessions.begin() as session:
        session.add(InventoryDailySheetSnapshotModel(
            tenant_id="tenant-a", business_date=day, external_source_id="source-a",
            source_spreadsheet_file_id="shared-live", snapshot_file_id=f"immutable-{day}",
            gemini_file_id=f"copy-{day}", status="completed",
            gemini_reconcile_status="failed",
        ))
        session.add(InventoryJobModel(
            tenant_id="tenant-a", job_type="inventory_v5_evening_reconcile_slot",
            entity_type="inventory_v5_scheduler_slot", entity_id=f"{day}:evening_reconcile",
            idempotency_key=f"evening-{day}", status=status,
            attempt_count=12, max_attempts=12, last_error_code=code,
        ))


def test_auto_discovers_only_prior_day_transient_failures_and_stays_idempotent():
    tmp, engine, sessions = _sessions()
    try:
        yesterday = date(2030, 8, 9)
        today = date(2030, 8, 10)
        _failed_evening(sessions, yesterday, status="retry")
        _failed_evening(sessions, today)
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        now = datetime(2030, 8, 10, 10, tzinfo=timezone.utc)
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=now) == 1
        active = queue.active_jobs("tenant-a")
        assert len(active) == 1 and active[0]["business_date"] == yesterday.isoformat()
        assert active[0]["promote"] is True
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=now) == 0
        assert queue.enqueue_failed_evening_days(frozenset({"another-tenant"}), now=now) == 0
        with sessions.begin() as session:
            job = session.get(InventoryJobModel, active[0]["job_id"])
            job.status = "failed"
        # A terminal auto job must never be silently resurrected next minute.
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=now) == 0
    finally:
        engine.dispose()
        tmp.cleanup()


def test_auto_recovery_ignores_permanent_failure_and_respects_reset_window():
    tmp, engine, sessions = _sessions()
    try:
        yesterday = date(2030, 8, 9)
        older = date(2030, 8, 8)
        _failed_evening(sessions, yesterday, code="inventory_gemini_provider_rejected")
        _failed_evening(sessions, older, code="inventory_gemini_rate_limited")
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        before_reset = datetime(2030, 8, 9, 21, tzinfo=timezone.utc)  # 04:00 local on Aug 10
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=before_reset) == 1
        assert queue.active_jobs("tenant-a")[0]["business_date"] == older.isoformat()
        assert queue.enqueue_failed_evening_days(
            frozenset({"tenant-a"}), now=datetime(2030, 8, 10, 10, tzinfo=timezone.utc),
        ) == 0
    finally:
        engine.dispose()
        tmp.cleanup()




def test_auto_waits_for_previous_day_lifecycle_cutoff_before_copying():
    tmp, engine, sessions = _sessions()
    try:
        previous = date(2030, 8, 9)
        _failed_evening(sessions, previous, code="inventory_gemini_rate_limited", status="retry")
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        before_cutoff = datetime(2030, 8, 9, 21, tzinfo=timezone.utc)  # 04:00 local Aug 10
        after_cutoff = datetime(2030, 8, 9, 23, tzinfo=timezone.utc)  # 06:00 local Aug 10
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=before_cutoff) == 0
        assert queue.last_auto_scan["before_safe_boundary"] == 1
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=after_cutoff) == 1
    finally:
        engine.dispose()
        tmp.cleanup()



def test_recent_replay_failure_diagnostics_exposes_only_safe_reason_tokens():
    tmp, engine, sessions = _sessions()
    try:
        with sessions.begin() as session:
            session.add(InventoryJobModel(
                tenant_id="tenant-a", job_type="inventory_v4_historical_replay",
                entity_type="daily_sheet_snapshot", entity_id="2030-08-09",
                idempotency_key="history-a", status="failed",
                last_error_code="inventory_sheet_agent_v4_safety_error",
                last_error_message="configured_sheet_not_found",
                created_at=datetime(2030, 8, 10, tzinfo=timezone.utc),
            ))
            session.add(InventoryJobModel(
                tenant_id="tenant-a", job_type="inventory_v4_historical_replay",
                entity_type="daily_sheet_snapshot", entity_id="2030-08-08",
                idempotency_key="history-b", status="failed",
                last_error_code="unknown",
                last_error_message="token=my-secret-sensitive-value",
                created_at=datetime(2030, 8, 10, tzinfo=timezone.utc),
            ))
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        info = queue.recent_failure_reasons(
            frozenset({"tenant-a"}), now=datetime(2030, 8, 10, 10, tzinfo=timezone.utc)
        )
        assert [item["reason"] for item in info] == [
            "configured_sheet_not_found", "unstructured_error",
        ]
        assert queue.recent_failure_reasons(frozenset({"other-tenant"}), now=datetime(2030, 8, 10, 10, tzinfo=timezone.utc)) == []
    finally:
        engine.dispose()
        tmp.cleanup()


def test_legacy_in_progress_replay_is_retried_not_terminal():
    from app.modules.inventory.daily_sheet.service import DailySheetConfigurationError
    tmp, engine, sessions = _sessions()
    try:
        class Service:
            def replay_agent_v4_historical(self, *_args, **_kwargs):
                raise DailySheetConfigurationError("inventory_historical_replay_in_progress")
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: Service())
        job = queue.enqueue("tenant-a", date(2030, 8, 9))
        assert queue.run_once(frozenset({"tenant-a"})) == 0
        result = queue.status("tenant-a", job["job_id"])
        assert result["status"] == "retry"
        assert result["last_error_code"] == "inventory_historical_replay_in_progress"
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
