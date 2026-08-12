from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
from app.modules.inventory.persistence_model import InventorySettingsModel


class InventoryDailySchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'scheduler.db'}")
        event.listen(
            self.engine,
            "connect",
            lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
        )
        keep = {
            "tenants",
            "external_sources",
            "inventory_settings",
            "inventory_documents",
            "inventory_daily_runs",
            "inventory_daily_run_events",
            "inventory_reviews",
            "inventory_transactions",
            "inventory_jobs",
        }
        for table in Base.metadata.sorted_tables:
            if table.name in keep:
                table.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
            session.add(
                ExternalSourceModel(
                    id="source-a",
                    tenant_id="tenant-a",
                    source_key="source-a",
                    source_type="google_drive",
                )
            )
            session.add(
                InventorySettingsModel(
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    inbox_folder_id="inbox",
                    enabled=True,
                )
            )
        self.scheduler = InventoryDailyScheduler(self.sessions)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_before_cutoff_is_noop_then_restarts_are_idempotent(self) -> None:
        self.assertEqual(0, self.scheduler.run_once(datetime(2030, 8, 9, 9, 0, tzinfo=timezone.utc)))
        after = datetime(2030, 8, 9, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(1, self.scheduler.run_once(after))
        self.assertEqual(1, self.scheduler.run_once(after))

    def test_standalone_scheduler_entrypoint_registers_tenant_mapping(self) -> None:
        root = Path(__file__).resolve().parents[5]
        command = [
            sys.executable,
            "-c",
            (
                f"import runpy; runpy.run_path({str(root / 'apps/inventory_scheduler/main.py')!r}); "
                "from app.core.database import Base; "
                "assert 'tenants' in Base.metadata.tables"
            ),
        ]
        result = subprocess.run(
            command,
            env={**os.environ, "PYTHONPATH": str(root / "apps/api")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
