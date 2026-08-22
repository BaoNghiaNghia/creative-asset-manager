from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
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
