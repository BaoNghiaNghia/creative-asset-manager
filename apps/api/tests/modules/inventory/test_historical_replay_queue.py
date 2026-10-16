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
from app.modules.inventory.persistence_model import InventorySettingsModel, InventoryDailySheetSnapshotModel, InventoryDailyCarryForwardModel
from app.modules.inventory.daily_sheet.replay_queue import HistoricalReplayQueue


def _sessions():
    tmp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(tmp.name) / 'queue.db'}")
    event.listen(engine, "connect", lambda con, _: con.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants", "oauth_connections", "external_sources", "inventory_settings", "inventory_jobs", "inventory_daily_sheet_snapshots", "inventory_daily_carry_forwards"):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
        session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive"))
        session.add(InventorySettingsModel(
            tenant_id="tenant-a", enabled=True, image_pipeline_enabled=False,
            daily_sheet_automation_enabled=True, external_source_id="source-a", inbox_folder_id="x",
            daily_sheet_config_json={"version": 4, "agent": {"apply_mode": "auto"}},
        ))
    return tmp, engine, sessions


def test_promoting_replay_is_rejected_before_enqueue_in_shadow_or_review_mode():
    import pytest
    tmp, engine, sessions = _sessions()
    try:
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        for mode in ("shadow", "review"):
            with sessions.begin() as session:
                settings = session.scalar(select(InventorySettingsModel))
                settings.daily_sheet_config_json = {"version": 4, "agent": {"apply_mode": mode}}
            with pytest.raises(ValueError, match="inventory_historical_replay_requires_auto_apply_mode"):
                queue.enqueue("tenant-a", date(2030, 8, 9), promote=True)
            with sessions() as session:
                assert session.scalars(select(InventoryJobModel)).all() == []
        sandbox = queue.enqueue("tenant-a", date(2030, 8, 9), promote=False)
        assert sandbox["status"] == "pending"
        assert sandbox["promote"] is False
    finally:
        engine.dispose()
        tmp.cleanup()


def test_automatic_replay_skips_shadow_mode_without_creating_jobs():
    tmp, engine, sessions = _sessions()
    try:
        day = date(2030, 8, 9)
        _failed_evening(sessions, day)
        with sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel))
            settings.daily_sheet_config_json = {"version": 4, "agent": {"apply_mode": "shadow"}}
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        moment = datetime(2030, 8, 10, 10, tzinfo=timezone.utc)
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=moment) == 0
        assert queue.last_auto_scan["apply_mode_not_auto"] == 1
        assert queue.last_auto_scan["snapshots"] == 1
        assert queue.last_auto_scan["v4_tenants"] == 1
        with sessions() as session:
            assert session.scalars(select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v4_historical_replay_request"
            )).all() == []
        with sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel))
            settings.daily_sheet_config_json = {"version": 4, "agent": {"apply_mode": "auto"}}
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=moment) == 1
    finally:
        engine.dispose()
        tmp.cleanup()


def test_date_inspection_reads_old_backlog_and_reset_without_leaking_identifiers():
    tmp, engine, sessions = _sessions()
    try:
        old_day, reset_day = date(2030, 8, 1), date(2030, 8, 2)
        _failed_evening(sessions, old_day)
        with sessions.begin() as session:
            session.add(InventoryJobModel(
                tenant_id="tenant-a", job_type="inventory_v4_historical_replay_request",
                entity_type="inventory_daily_sheet_snapshot", entity_id=old_day.isoformat(),
                idempotency_key="replay-old", status="completed",
                payload_json={
                    "promote": True,
                    "result": {"status": "review_required", "verification_status": "not_executed",
                               "promoted": False, "review_issue_codes": ["no_actionable_assessment", "TOKEN secret 12"]},
                },
            ))
            session.add(InventoryDailyCarryForwardModel(
                tenant_id="tenant-a", target_business_date=reset_day,
                previous_business_date=old_day, idempotency_key="carry-old",
                status="review_required", error_code="previous_day_gemini_not_verified",
            ))
        queue = HistoricalReplayQueue(sessions)
        result = queue.inspect_dates(frozenset({"tenant-a"}), (old_day, reset_day))
        assert len(result) == 2
        backlog = result[0]["profiles"][0]
        assert backlog["apply_mode"] == "auto"
        assert backlog["immutable_snapshot_ready"] is True
        assert backlog["gemini_verified"] is False
        assert backlog["replay_status"] == "review_required"
        assert backlog["replay_verification"] == "not_executed"
        assert backlog["review_issue_codes"] == ["no_actionable_assessment"]
        assert result[1]["profiles"][0]["morning_reset_status"] == "review_required"
        assert result[1]["profiles"][0]["morning_reset_error"] == "previous_day_gemini_not_verified"
        assert queue.inspect_dates(frozenset({"other-tenant"}), (old_day,))[0]["profiles"] == []
        assert queue.inspect_dates(frozenset(), (old_day,))[0]["profiles"] == []
        assert "tenant-a" not in str(result)
        assert "immutable-2030" not in str(result)
        assert "TOKEN secret" not in str(result)
    finally:
        engine.dispose()
        tmp.cleanup()


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



def test_unverified_legacy_completed_job_renders_as_review_required():
    tmp, engine, sessions = _sessions()
    try:
        queue = HistoricalReplayQueue(sessions)
        request = queue.enqueue("tenant-a", date(2030, 8, 9))
        with sessions.begin() as session:
            job = session.get(InventoryJobModel, request["job_id"])
            job.status = "completed"
            job.payload_json = {**job.payload_json, "result": {
                "status": "review_required", "verification_status": "not_executed",
                "promoted": False, "apply_mode": "auto",
                "review_issue_codes": ["no_actionable_assessment", "unsafe details: private cells"],
            }}
        assert queue.status("tenant-a", request["job_id"])["status"] == "review_required"
        diagnostic = queue.recent_replay_results(frozenset({"tenant-a"}))[0]
        assert diagnostic["status"] == "review_required"
        assert diagnostic["apply_mode"] == "auto"
        assert diagnostic["review_issue_codes"] == ["no_actionable_assessment"]
        with sessions() as session:
            assert session.get(InventoryJobModel, request["job_id"]).status == "completed"
    finally:
        engine.dispose()
        tmp.cleanup()


def test_unverified_replay_requires_review_and_never_counts_as_completed():
    tmp, engine, sessions = _sessions()
    try:
        class Service:
            def replay_agent_v4_historical(self, *_args, **_kwargs):
                return {
                    "status": "review_required", "verification_status": "not_executed",
                    "promoted": False, "run_id": "review-run",
                }
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: Service())
        job = queue.enqueue("tenant-a", date(2030, 8, 9), promote=True)
        assert queue.run_once(frozenset({"tenant-a"})) == 0
        row = queue.status("tenant-a", job["job_id"])
        assert row["status"] == "failed"
        assert row["last_error_code"] == "inventory_historical_replay_review_required"
        assert row["result"]["status"] == "review_required"
        assert row["result"]["promoted"] is False
        assert queue.run_once(frozenset({"tenant-a"})) == 0
    finally:
        engine.dispose()
        tmp.cleanup()


def test_sandbox_replay_with_review_result_is_completed_without_promotion():
    tmp, engine, sessions = _sessions()
    try:
        class Service:
            def replay_agent_v4_historical(self, *_args, **_kwargs):
                return {
                    "status": "review_required", "verification_status": "not_executed",
                    "promoted": False,
                }
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: Service())
        job = queue.enqueue("tenant-a", date(2030, 8, 9), promote=False)
        assert queue.run_once(frozenset({"tenant-a"})) == 1
        state = queue.status("tenant-a", job["job_id"])
        assert state["status"] == "completed"
        assert state["result"]["promoted"] is False
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



def test_historical_replay_diagnostics_fail_closed_on_unsafe_codes_and_promoted_text():
    """An untrusted provider response cannot become a 'promoted' audit or leak."""
    tmp, engine, sessions = _sessions()
    try:
        queue = HistoricalReplayQueue(sessions)
        day = date(2030, 8, 9)
        request = queue.enqueue("tenant-a", day, promote=True)
        with sessions.begin() as session:
            job = session.get(InventoryJobModel, request["job_id"])
            job.status = "completed"
            job.last_error_code = "Bearer SECRET marker from provider"
            job.payload_json = {**job.payload_json, "promote": "false", "result": {
                "status": "completed; private cell A9",
                "verification_status": "verified; bearer token",
                "promoted": "false",
            }}
            session.add(InventoryJobModel(
                tenant_id="tenant-a",
                job_type="inventory_v4_historical_replay",
                entity_type="daily_sheet_snapshot",
                entity_id=day.isoformat(),
                idempotency_key="test-untrusted-historical-code",
                status="failed",
                last_error_code="Bearer SECRET marker from provider",
                last_error_message="private cell payload",
            ))
        outcome = queue.recent_replay_results(frozenset({"tenant-a"}))[0]
        assert outcome["status"] == "review_required"
        assert outcome["promoted"] is False
        assert outcome["result_status"] == "unstructured_error"
        assert outcome["verification_status"] == "unstructured_error"
        assert outcome["error_code"] == "unstructured_error"
        assert "SECRET" not in str(outcome)
        failures = queue.recent_failure_reasons(frozenset({"tenant-a"}))
        assert failures[0]["error_code"] == "unstructured_error"
        assert failures[0]["reason"] == "unstructured_error"
        assert "SECRET" not in str(failures)
    finally:
        engine.dispose()
        tmp.cleanup()


def test_v2_automatically_retries_once_after_unknown_tool_safety_failure():
    tmp, engine, sessions = _sessions()
    try:
        day = date(2030, 8, 9)
        _failed_evening(sessions, day)
        with sessions.begin() as session:
            session.add(InventoryJobModel(
                tenant_id="tenant-a", job_type="inventory_v4_historical_replay_request",
                entity_type="daily_sheet_snapshot", entity_id=str(day),
                idempotency_key="inventory-replay-auto-v1:tenant-a:2030-08-09",
                status="failed", last_error_code="inventory_sheet_agent_v4_safety_error",
                payload_json={"automatic": True, "promote": True},
            ))
            session.add(InventoryJobModel(
                tenant_id="tenant-a", job_type="inventory_v4_historical_replay",
                entity_type="daily_sheet_snapshot", entity_id=str(day),
                idempotency_key="historical-failed", status="failed",
                last_error_message="unknown_tool",
            ))
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        now = datetime(2030, 8, 10, 10, tzinfo=timezone.utc)
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=now) == 1
        assert queue.active_jobs("tenant-a")[0]["business_date"] == str(day)
        assert queue.enqueue_failed_evening_days(frozenset({"tenant-a"}), now=now) == 0
    finally:
        engine.dispose()
        tmp.cleanup()


def test_v2_replans_once_for_unverified_review_required_but_never_auth_failures():
    tmp, engine, sessions = _sessions()
    try:
        review_day, auth_day = date(2030, 8, 9), date(2030, 8, 8)
        _failed_evening(sessions, review_day)
        _failed_evening(sessions, auth_day)
        with sessions.begin() as session:
            for day, status, code, result in (
                (review_day, "completed", None, {"status": "review_required", "promoted": False}),
                (auth_day, "failed", "inventory_gemini_auth_or_permission_error", {}),
            ):
                session.add(InventoryJobModel(
                    tenant_id="tenant-a", job_type="inventory_v4_historical_replay_request",
                    entity_type="daily_sheet_snapshot", entity_id=str(day),
                    idempotency_key=f"inventory-replay-auto-v1:tenant-a:{day}",
                    status=status, last_error_code=code,
                    payload_json={"automatic": True, "promote": True, "result": result},
                ))
        queue = HistoricalReplayQueue(sessions, service_factory=lambda: None)
        assert queue.enqueue_failed_evening_days(
            frozenset({"tenant-a"}), now=datetime(2030, 8, 10, 10, tzinfo=timezone.utc),
        ) == 1
        assert queue.active_jobs("tenant-a")[0]["business_date"] == str(review_day)
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
