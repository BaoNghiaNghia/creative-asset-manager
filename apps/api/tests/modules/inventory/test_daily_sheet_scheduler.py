from datetime import datetime, timezone
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
from app.modules.inventory.persistence_model import InventorySettingsModel


class DailySheetSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'daily-sheet-scheduler.db'}")
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        for name in ("tenants", "external_sources", "inventory_settings"):
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
