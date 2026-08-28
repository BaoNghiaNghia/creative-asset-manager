from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
from app.modules.inventory.daily_sheet.semantic import InventoryDailySheetSemanticAnalyzer
from app.modules.inventory.daily_sheet.agent_v4.tools import V4AgentSafetyError
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import InventorySettingsModel


class DailySheetSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'daily-sheet-scheduler.db'}")
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        for name in ("tenants", "external_sources", "inventory_settings", "inventory_jobs"):
            Base.metadata.tables[name].create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
            session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="source-a", source_type="google_drive"))
            session.add(InventorySettingsModel(
                tenant_id="tenant-a", external_source_id="source-a", inbox_folder_id="inbox",
                enabled=True, image_pipeline_enabled=False, daily_sheet_automation_enabled=True,
                daily_snapshot_time_local="05:50", daily_reconcile_time_local="07:00",
                timezone="Asia/Ho_Chi_Minh",
            ))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_schedule_respects_ict_and_snapshot_precedes_reconcile(self):
        class Sheets:
            calls = []
            def snapshot_and_reset(self, tenant_id, business_date):
                self.calls.append(("snapshot", tenant_id, business_date.isoformat()))
                return SimpleNamespace(status="completed")
            def reconcile(self, tenant_id, business_date):
                self.calls.append(("reconcile", tenant_id, business_date.isoformat()))
                return {}
        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        self.assertEqual(0, scheduler.run_once(datetime(2030, 8, 8, 22, 49, tzinfo=timezone.utc)))
        self.assertEqual(1, scheduler.run_once(datetime(2030, 8, 8, 22, 50, tzinfo=timezone.utc)))
        self.assertEqual(2, scheduler.run_once(datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)))
        self.assertEqual(["snapshot", "snapshot", "reconcile"], [call[0] for call in sheets.calls])
        self.assertEqual("2030-08-08", sheets.calls[-1][2])


    def test_default_sheet_service_uses_one_production_semantic_analyzer(self):
        scheduler = InventoryDailyScheduler(self.sessions)

        self.assertIsInstance(
            scheduler.sheet_service.semantic_analyzer,
            InventoryDailySheetSemanticAnalyzer,
        )
        self.assertIs(
            scheduler.sheet_service.material_semantic_matcher.__self__,
            scheduler.sheet_service.semantic_analyzer,
        )

    def test_explicit_sheet_service_is_preserved_without_constructing_default(self):
        class FalsySheetService:
            def __bool__(self):
                return False

        supplied = FalsySheetService()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=supplied)

        self.assertIs(supplied, scheduler.sheet_service)

    def test_scheduler_wires_disabled_semantic_analyzer_fail_closed_without_google(self):
        class DisabledAnalyzer:
            enabled = False

            def match_material(self, _payload):
                return None

            def analyze_quantity(self, _tenant_id, _payload):
                return None

            def analyze_schema(self, _tenant_id, _payload):
                return None

        analyzer = DisabledAnalyzer()
        from unittest.mock import patch
        with patch(
            "app.modules.inventory.daily.scheduler.build_daily_sheet_semantic_analyzer",
            return_value=analyzer,
        ) as builder:
            scheduler = InventoryDailyScheduler(self.sessions)

        builder.assert_called_once_with(session_factory=self.sessions)
        self.assertIs(analyzer, scheduler.sheet_service.semantic_analyzer)
        self.assertIsNone(analyzer.analyze_quantity("tenant-a", {"raw": "unknown"}))

    def test_v3_shadow_scheduler_plans_once_and_never_calls_legacy_reconcile(self):
        with self.sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == "tenant-a"))
            settings.daily_sheet_config_json = {
                "version": 3,
                "mode": "gemini_sheet_agent",
                "source": {"sheet": "Daily", "range": "A1:H40"},
                "agent": {"apply_mode": "shadow"},
            }

        class Sheets:
            def __init__(self):
                self.calls = []
            def snapshot_and_reset(self, tenant_id, business_date):
                self.calls.append(("agent", tenant_id, business_date.isoformat()))
                return SimpleNamespace(status="shadow")
            def reconcile(self, tenant_id, business_date):
                raise AssertionError("V3 must not enter legacy reconciliation")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(1, scheduler.run_once(moment))
        self.assertEqual(0, scheduler.run_once(moment))
        self.assertEqual([("agent", "tenant-a", "2030-08-08")], sheets.calls)

    def test_v3_scheduler_retries_failed_plan_before_marking_date_complete(self):
        with self.sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == "tenant-a"
            ))
            settings.daily_sheet_config_json = {
                "version": 3,
                "mode": "gemini_sheet_agent",
                "source": {"sheet": "Daily", "range": "A1:H40"},
            }

        class Sheets:
            def __init__(self):
                self.calls = 0
            def snapshot_and_reset(self, tenant_id, business_date):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("planner_unavailable")
                return SimpleNamespace(status="shadow")
            def reconcile(self, tenant_id, business_date):
                raise AssertionError("V3 must not enter legacy reconciliation")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(0, scheduler.run_once(moment))
        self.assertEqual(1, scheduler.run_once(moment))
        self.assertEqual(2, sheets.calls)


    def test_v4_scheduler_uses_only_the_live_v4_executor_and_retries_failures(self):
        with self.sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == "tenant-a"
            ))
            settings.daily_sheet_config_json = {
                "version": 4,
                "mode": "gemini_tool_sheet_agent",
                "source": {"allowed_sheets": ["Daily"]},
                "agent": {"apply_mode": "auto"},
            }

        class Sheets:
            def __init__(self):
                self.calls = 0

            def run_agent_v4(self, tenant_id, business_date, *, slot_kind=None):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(status="review_required")
                return SimpleNamespace(status="completed")

            def snapshot_and_reset(self, *_args, **_kwargs):
                raise AssertionError("V4 must not enter legacy snapshot/reset")

            def reconcile(self, *_args, **_kwargs):
                raise AssertionError("V4 must not enter legacy reconciliation")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(1, scheduler.run_once(moment))
        self.assertEqual(0, scheduler.run_once(moment))
        self.assertEqual(1, sheets.calls)

    def _enable_v4(self):
        with self.sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == "tenant-a"
            ))
            settings.daily_sheet_config_json = {
                "version": 4,
                "mode": "gemini_tool_sheet_agent",
                "source": {"allowed_sheets": ["Daily"]},
                "agent": {"apply_mode": "auto"},
            }

    def test_v4_one_shot_settlement_survives_scheduler_restart(self):
        self._enable_v4()

        class Sheets:
            def __init__(self):
                self.calls = []

            def run_agent_v4(self, tenant_id, business_date, *, slot_kind=None):
                self.calls.append((tenant_id, business_date.isoformat(), slot_kind))
                return SimpleNamespace(status="completed", writes=0)

        sheets = Sheets()
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(1, InventoryDailyScheduler(
            self.sessions, sheet_service=sheets
        ).execute_v4_slot("reconcile", moment))
        self.assertEqual(0, InventoryDailyScheduler(
            self.sessions, sheet_service=sheets
        ).execute_v4_slot("reconcile", moment))
        self.assertEqual([("tenant-a", "2030-08-08", "reconcile")], sheets.calls)
        with self.sessions() as session:
            job = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v41_reconcile_slot"
            ))
            self.assertEqual("completed", job.status)
            self.assertEqual(1, job.attempt_count)

    def test_v4_snapshot_and_reconcile_are_independent_and_next_day_is_eligible(self):
        self._enable_v4()

        class Sheets:
            def __init__(self):
                self.calls = []

            def run_agent_v4(self, tenant_id, business_date, *, slot_kind=None):
                self.calls.append((business_date.isoformat(), slot_kind))
                return SimpleNamespace(status="completed", writes=0)

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        first = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(1, scheduler.execute_v4_slot("snapshot", first))
        self.assertEqual(1, scheduler.execute_v4_slot("reconcile", first))
        self.assertEqual(1, scheduler.execute_v4_slot("snapshot", first + timedelta(days=1)))
        self.assertEqual([
            ("2030-08-08", "snapshot"),
            ("2030-08-08", "reconcile"),
            ("2030-08-09", "snapshot"),
        ], sheets.calls)

    def test_v4_stale_evidence_uses_persisted_retry_not_immediate_restart(self):
        self._enable_v4()

        class Sheets:
            def __init__(self):
                self.calls = 0

            def run_agent_v4(self, *_args, **_kwargs):
                self.calls += 1
                raise V4AgentSafetyError("stale_evidence")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(
            self.sessions, sheet_service=sheets
        )
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(V4AgentSafetyError, msg="stale_evidence"):
            scheduler.execute_v4_slot("reconcile", moment)
        self.assertEqual(1, sheets.calls)
        with self.sessions() as session:
            job = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v41_reconcile_slot"
            ))
            self.assertEqual("retry", job.status)
            self.assertEqual("stale_evidence", job.last_error_code)

    def test_v4_transient_failure_backs_off_without_hot_loop(self):
        self._enable_v4()

        class Sheets:
            def __init__(self):
                self.calls = 0

            def run_agent_v4(self, *_args, **_kwargs):
                self.calls += 1
                raise TimeoutError("temporary Google timeout")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(TimeoutError):
            scheduler.execute_v4_slot("reconcile", moment)
        self.assertEqual(0, scheduler.execute_v4_slot("reconcile", moment))
        self.assertEqual(1, sheets.calls)
        with self.sessions() as session:
            job = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v41_reconcile_slot"
            ))
            self.assertEqual("retry", job.status)
            self.assertGreater(job.next_attempt_at.replace(tzinfo=timezone.utc), moment)

    def test_v4_gemini_protocol_failures_are_retryable(self):
        for code in (
            "inventory_sheet_agent_v4_missing_tool_call",
            "inventory_sheet_agent_v4_round_limit",
        ):
            with self.subTest(code=code):
                self.assertTrue(
                    InventoryDailyScheduler._retryable_v4_error(RuntimeError(code))
                )

    def test_v4_permanent_failure_is_terminal_without_hot_loop(self):
        self._enable_v4()

        class Sheets:
            def __init__(self):
                self.calls = 0

            def run_agent_v4(self, *_args, **_kwargs):
                self.calls += 1
                raise ValueError("spreadsheet_not_authorized")

        sheets = Sheets()
        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=sheets)
        moment = datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            scheduler.execute_v4_slot("reconcile", moment)
        self.assertEqual(0, scheduler.execute_v4_slot("reconcile", moment))
        self.assertEqual(1, sheets.calls)
        with self.sessions() as session:
            job = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v41_reconcile_slot"
            ))
            self.assertEqual("failed", job.status)

    def test_v3_scheduler_ignores_tenant_when_daily_automation_is_disabled(self):
        with self.sessions.begin() as session:
            settings = session.scalar(select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == "tenant-a"
            ))
            settings.daily_sheet_automation_enabled = False
            settings.daily_sheet_config_json = {
                "version": 3,
                "mode": "gemini_sheet_agent",
                "source": {"sheet": "Daily", "range": "A1:H40"},
                "agent": {"apply_mode": "shadow"},
            }

        class Sheets:
            def snapshot_and_reset(self, *_args, **_kwargs):
                raise AssertionError("disabled scheduler must not plan")

            def reconcile(self, *_args, **_kwargs):
                raise AssertionError("disabled scheduler must not reconcile")

        scheduler = InventoryDailyScheduler(self.sessions, sheet_service=Sheets())

        self.assertEqual(
            0,
            scheduler.run_once(datetime(2030, 8, 9, 0, 0, tzinfo=timezone.utc)),
        )
