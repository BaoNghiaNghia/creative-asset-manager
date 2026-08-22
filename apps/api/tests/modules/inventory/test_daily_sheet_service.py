from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.inventory.daily_sheet.google_client import GOOGLE_SHEETS_SCOPE, NATIVE_SPREADSHEET_MIME
from app.modules.inventory.daily_sheet.parser import DailySheetValidationError
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.persistence_model import InventoryDailySheetReconciliationModel, InventoryDailySheetSnapshotModel, InventorySettingsModel


CONFIG = {
    "version": 1,
    "source_ranges": [{
        "sheet": "Stock", "range": "A1:C20", "header_row": 1,
        "sku_column": "SKU", "quantity_column": "Quantity", "warehouse_column": "Warehouse",
    }],
    "reset": {"mode": "clear_ranges", "ranges": ["Stock!D2:D20"]},
    "targets": [{
        "warehouse": "Main Warehouse", "sheet": "Target",
        "sku_range": "Target!A2:A20", "quantity_column": "F",
    }],
    "new_sku_policy": "reject",
}


class FakeGoogle:
    def __init__(self, mismatch=False, fail_clear_once=False):
        self.modified = "2030-08-09T00:00:00Z"
        self.values = {
            ("working", "Stock!A1:C20"): [["SKU", "Quantity", "Warehouse"], ["A", 5, "Main Warehouse"]],
            ("working", "Stock!D2:D20"): [["reset-me"]],
            ("target", "Target!A2:A20"): [["A"]],
        }
        self.mismatch = mismatch
        self.copy_calls = 0
        self.clear_calls = 0
        self.update_calls = []
        self.fail_clear_once = fail_clear_once

    def __enter__(self): return self
    def __exit__(self, *_): return None

    def validate_native_spreadsheet(self, file_id):
        return {"id": file_id, "name": "Inventory", "mimeType": NATIVE_SPREADSHEET_MIME, "modifiedTime": self.modified}

    def drive_file(self, file_id):
        mime_type = "application/vnd.google-apps.folder" if file_id == "archive" else NATIVE_SPREADSHEET_MIME
        return {"id": file_id, "mimeType": mime_type, "modifiedTime": self.modified}

    def spreadsheet_metadata(self, file_id): return {"spreadsheetId": file_id}
    def ensure_archive_folder(self, *_args, **_kwargs): return {"id": "archive-date"}

    def copy_spreadsheet(self, source_id, **_kwargs):
        self.copy_calls += 1
        copied = [list(row) for row in self.values[(source_id, "Stock!A1:C20")]]
        if self.mismatch:
            copied[-1][1] = 999
        self.values[("snapshot", "Stock!A1:C20")] = copied
        return {"id": "snapshot"}

    def batch_get_values(self, file_id, ranges, **_kwargs):
        result = []
        for cell_range in ranges:
            values = self.values.get((file_id, cell_range), [])
            result.append({"range": cell_range, "values": [list(row) for row in values]})
        return result

    def batch_clear_values(self, file_id, ranges):
        self.clear_calls += 1
        if self.fail_clear_once:
            self.fail_clear_once = False
            raise RuntimeError("transient reset failure")
        for cell_range in ranges:
            self.values[(file_id, cell_range)] = []
        return {}

    def batch_update_values(self, file_id, updates):
        self.update_calls.append(list(updates))
        for update in updates:
            self.values[(file_id, update["range"])] = [list(row) for row in update["values"]]
        return {}


@pytest.fixture
def daily_sheet_db():
    temporary = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temporary.name) / 'daily-sheet.db'}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    for name in (
        "tenants", "external_sources", "oauth_connections", "inventory_settings",
        "inventory_daily_sheet_snapshots", "inventory_daily_sheet_reconciliations",
    ):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        session.add(ExternalSourceModel(
            id="source-a", tenant_id="tenant-a", source_key="source-a", source_type="google_drive",
            source_metadata={"oauth_connection_id": "connection-a"},
        ))
        session.add(OAuthConnectionModel(
            id="connection-a", tenant_id="tenant-a", provider="google",
            provider_account_id="account-a", key_version="v1", status="active",
            scopes_json=[GOOGLE_SHEETS_SCOPE],
        ))
        session.add(InventorySettingsModel(
            tenant_id="tenant-a", enabled=True, image_pipeline_enabled=True,
            daily_sheet_automation_enabled=True, external_source_id="source-a",
            inbox_folder_id="inbox", daily_working_spreadsheet_file_id="working",
            daily_archive_root_folder_id="archive", daily_target_spreadsheet_file_id="target",
            daily_sheet_config_json=CONFIG,
        ))
    yield sessions
    engine.dispose()
    temporary.cleanup()


def service(sessions, google, now=None):
    return InventoryDailySheetService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "token",
        clock=lambda: now or datetime(2030, 8, 10, tzinfo=timezone.utc),
    )


def test_snapshot_is_verified_before_reset_and_repeated_run_is_idempotent(daily_sheet_db):
    google = FakeGoogle()
    worker = service(daily_sheet_db, google)
    result = worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert result.status == "completed"
    assert google.copy_calls == 1
    assert google.clear_calls == 1
    assert worker.snapshot_and_reset("tenant-a", date(2030, 8, 9)).id == result.id
    assert google.copy_calls == 1
    assert google.clear_calls == 1


def test_snapshot_mismatch_never_resets_source(daily_sheet_db):
    google = FakeGoogle(mismatch=True)
    with pytest.raises(DailySheetValidationError, match="snapshot_verification_mismatch"):
        service(daily_sheet_db, google).snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert google.clear_calls == 0
    with daily_sheet_db() as session:
        row = session.scalar(select(InventoryDailySheetSnapshotModel))
        assert row.status == "terminal_failure"


def test_reset_failure_reuses_verified_snapshot_on_retry(daily_sheet_db):
    google = FakeGoogle(fail_clear_once=True)
    worker = service(daily_sheet_db, google)
    with pytest.raises(RuntimeError, match="transient reset failure"):
        worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))

    with daily_sheet_db() as session:
        row = session.scalar(select(InventoryDailySheetSnapshotModel))
        assert row.status == "retryable_failure"
        assert row.snapshot_file_id == "snapshot"
        assert row.verified_at is not None
        assert row.reset_started_at is not None

    result = worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert result.status == "completed"
    assert google.copy_calls == 1
    assert google.clear_calls == 2


def test_stale_reset_is_resumed_without_recopying(daily_sheet_db):
    google = FakeGoogle()
    with daily_sheet_db.begin() as session:
        session.add(InventoryDailySheetSnapshotModel(
            tenant_id="tenant-a", business_date=date(2030, 8, 9),
            external_source_id="source-a", source_spreadsheet_file_id="working",
            snapshot_file_id="snapshot", status="resetting",
            verified_at=datetime(2030, 8, 9, tzinfo=timezone.utc),
            reset_started_at=datetime(2030, 8, 9, tzinfo=timezone.utc),
            updated_at=datetime(2030, 8, 9, tzinfo=timezone.utc),
        ))
    result = service(daily_sheet_db, google).snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert result.status == "completed"
    assert google.copy_calls == 0
    assert google.clear_calls == 1


def test_disabled_configuration_can_be_validated_before_enable(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        session.scalar(select(InventorySettingsModel)).daily_sheet_automation_enabled = False
    report = service(daily_sheet_db, FakeGoogle()).validate_configuration("tenant-a")
    assert report["valid"] is True


def test_reconciliation_writes_absolute_values_once_and_is_idempotent(daily_sheet_db):
    google = FakeGoogle()
    google.values[("previous", "Stock!A1:C20")] = [["SKU", "Quantity", "Warehouse"], ["A", 2, "Main Warehouse"], ["B", 7, "Main Warehouse"]]
    google.values[("current", "Stock!A1:C20")] = [["SKU", "Quantity", "Warehouse"], ["A", 5, "Main Warehouse"]]
    google.values[("target", "Target!A2:A20")] = [["A"], ["B"]]
    google.values[("target", "Target!F2")] = [[1]]
    google.values[("target", "Target!F3")] = [[7]]
    with daily_sheet_db.begin() as session:
        session.add_all([
            InventoryDailySheetSnapshotModel(
                id="previous-run", tenant_id="tenant-a", business_date=date(2030, 8, 8),
                external_source_id="source-a", source_spreadsheet_file_id="working",
                snapshot_file_id="previous", status="completed",
            ),
            InventoryDailySheetSnapshotModel(
                id="current-run", tenant_id="tenant-a", business_date=date(2030, 8, 9),
                external_source_id="source-a", source_spreadsheet_file_id="working",
                snapshot_file_id="current", status="completed",
            ),
        ])
    worker = service(daily_sheet_db, google)
    first = worker.reconcile("tenant-a", date(2030, 8, 9))
    assert first["status"] == "completed"
    assert first["writes"] == 2
    assert google.values[("target", "Target!F2")] == [["5"]]
    assert google.values[("target", "Target!F3")] == [["0"]]
    assert first["variances"][0]["variance"] == "3"
    assert first["variances"][1]["variance"] == "-7"
    second = worker.reconcile("tenant-a", date(2030, 8, 9))
    assert second["status"] == "completed"
    assert len(google.update_calls) == 1


def test_missing_previous_snapshot_requires_explicit_baseline(daily_sheet_db):
    google = FakeGoogle()
    with daily_sheet_db.begin() as session:
        session.add(InventoryDailySheetSnapshotModel(
            id="current-run", tenant_id="tenant-a", business_date=date(2030, 8, 9),
            external_source_id="source-a", source_spreadsheet_file_id="working",
            snapshot_file_id="current", status="completed",
        ))
    worker = service(daily_sheet_db, google)
    result = worker.reconcile("tenant-a", date(2030, 8, 9))
    assert result["status"] == "awaiting_baseline"
    baseline = worker.set_baseline("tenant-a", "current-run")
    assert baseline["status"] == "baseline_selected"


def test_recent_writing_reconciliation_is_not_double_written(daily_sheet_db):
    google = FakeGoogle()
    google.values[("previous", "Stock!A1:C20")] = [["SKU", "Quantity", "Warehouse"], ["A", 2, "Main Warehouse"]]
    google.values[("current", "Stock!A1:C20")] = [["SKU", "Quantity", "Warehouse"], ["A", 5, "Main Warehouse"]]
    google.values[("target", "Target!F2")] = [[1]]
    with daily_sheet_db.begin() as session:
        session.add_all([
            InventoryDailySheetSnapshotModel(id="previous-run", tenant_id="tenant-a", business_date=date(2030, 8, 8), external_source_id="source-a", source_spreadsheet_file_id="working", snapshot_file_id="previous", status="completed"),
            InventoryDailySheetSnapshotModel(id="current-run", tenant_id="tenant-a", business_date=date(2030, 8, 9), external_source_id="source-a", source_spreadsheet_file_id="working", snapshot_file_id="current", status="completed"),
        ])
    with daily_sheet_db.begin() as session:
        session.add(InventoryDailySheetReconciliationModel(
            tenant_id="tenant-a", business_date=date(2030, 8, 9),
            current_snapshot_id="current-run", previous_snapshot_id="previous-run",
            status="writing", updated_at=datetime(2030, 8, 10, tzinfo=timezone.utc),
        ))
    result = service(daily_sheet_db, google).reconcile("tenant-a", date(2030, 8, 9))
    assert result == {"status": "in_progress", "writes": 0}
    assert google.update_calls == []


def test_restore_template_uses_formula_rendering_and_verifies(daily_sheet_db):
    google = FakeGoogle()
    google.values[("template", "Stock!D2:D20")] = [["=SUM(A2:C2)"]]
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_template_spreadsheet_file_id = "template"
        settings.daily_sheet_config_json = {**CONFIG, "reset": {"mode": "restore_template", "ranges": ["Stock!D2:D20"]}}
    result = service(daily_sheet_db, google).snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert result.status == "completed"
    assert google.values[("working", "Stock!D2:D20")] == [["=SUM(A2:C2)"]]
    assert len(google.update_calls) == 1


def test_status_exposes_business_schedule_and_safe_drive_links(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        session.add(InventoryDailySheetSnapshotModel(
            id="status-snapshot",
            tenant_id="tenant-a",
            business_date=date(2030, 8, 9),
            external_source_id="source-a",
            source_spreadsheet_file_id="working",
            archive_folder_id="archive-date",
            snapshot_file_id="snapshot",
            status="completed",
            reset_completed_at=datetime(2030, 8, 10, tzinfo=timezone.utc),
        ))

    result = service(
        daily_sheet_db,
        FakeGoogle(),
        now=datetime(2030, 8, 9, 22, 0, tzinfo=timezone.utc),
    ).status("tenant-a")

    assert result["working_business_date"] == "2030-08-09"
    assert result["next_snapshot_at"].startswith("2030-08-10T05:50:00+07:00")
    assert result["next_reconciliation_at"].startswith("2030-08-10T07:00:00+07:00")
    assert result["working_spreadsheet_url"].endswith("/working/edit")
    assert result["last_snapshot"]["snapshot_url"].endswith("/snapshot/edit")
    assert result["last_snapshot"]["archive_folder_url"].endswith("/archive-date")
