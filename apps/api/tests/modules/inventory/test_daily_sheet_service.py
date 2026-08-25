from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.inventory.daily_sheet.google_client import GOOGLE_SHEETS_SCOPE, NATIVE_SPREADSHEET_MIME
from app.modules.inventory.daily_sheet.parser import DailyCountSheetValidationError, DailySheetValidationError
from app.modules.inventory.daily_sheet.service import (
    DailySheetConfigurationError,
    InventoryDailySheetService,
)
from app.modules.inventory.persistence_model import (
    InventoryDailySheetReconciliationModel,
    InventoryDailySheetSnapshotModel,
    InventoryItemModel,
    InventoryMaterialCandidateModel,
    InventoryMaterialExternalIdentityModel,
    InventoryMaterialPackageConversionModel,
    InventorySettingsModel,
)


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
        "inventory_items", "inventory_item_aliases",
        "inventory_material_external_identities",
        "inventory_material_package_conversions",
        "inventory_material_candidates",
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

V2_HEADERS = ["STT", "Tên Nguyên Liệu / Vật Tư", "Phân Loại", "SL Đầu Ca / Nhận", "SL Sử Dụng Pha Chế", "Nhập Hàng", "SL Huỷ / Hư Hỏng", "Tồn Cuối Ca"]
V2_RANGE = "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!A1:H1000"
V2_CONFIG = {
    "version": 2, "mode": "daily_count_sheet",
    "source": {
        "sheet": "Bảng Kiểm Kê Nguyên Liệu Vật Tư", "range": "A1:H1000", "header_row": 1,
        "item_row": {"strategy": "numeric_key", "key_column": "STT"},
        "columns": dict(zip(("item_key", "name", "category", "opening", "used", "inbound", "waste", "closing"), V2_HEADERS, strict=True)),
        "warehouse": "main",
    },
    "stock": {"authoritative_column": "closing"},
    "reset": {"mode": "clear_entry_columns", "entry_columns": ["used", "inbound", "waste", "closing"]},
    "reconciliation": {"mode": "report_only"},
}


class FakeGoogleV2(FakeGoogle):
    def __init__(self):
        super().__init__()
        self.values[("working", V2_RANGE)] = [V2_HEADERS, [25, "Khúc bạch tảng", "Topping", "14", "", "", "", "14"]]

    def spreadsheet_metadata(self, file_id):
        return {
            "spreadsheetId": file_id,
            "properties": {"title": "Bảng Kiểm Kê Nguyên Liệu Vật Tư", "timeZone": "America/Los_Angeles"},
            "sheets": [{"properties": {"sheetId": 1, "title": "Bảng Kiểm Kê Nguyên Liệu Vật Tư"}}],
        }

    def copy_spreadsheet(self, source_id, **_kwargs):
        self.copy_calls += 1
        self.values[("snapshot", V2_RANGE)] = [
            list(row) for row in self.values[(source_id, V2_RANGE)]
        ]
        return {"id": "snapshot"}


def configure_v2(sessions):
    with sessions.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_config_json = V2_CONFIG
        settings.timezone = "Asia/Ho_Chi_Minh"


def test_v2_validation_returns_structured_timezone_warning(daily_sheet_db):
    configure_v2(daily_sheet_db)
    report = service(daily_sheet_db, FakeGoogleV2()).validate_configuration("tenant-a")
    assert report["valid"] is True
    assert report["errors"] == []
    assert any(item["code"] == "workbook_timezone_mismatch" for item in report["warnings"])


def test_v2_deterministic_row_parses_through_snapshot_and_reset(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    worker = service(daily_sheet_db, google)

    assert worker.validate_configuration("tenant-a")["valid"] is True
    result = worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))

    assert result.status == "completed"
    assert google.copy_calls == 1
    assert google.clear_calls == 1


def test_v2_report_only_compares_immutable_snapshots_without_writes(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    google.values[("snapshot-current", V2_RANGE)] = [V2_HEADERS, [25, "Khúc bạch tảng", "Topping", "", "", "", "", "14"]]
    google.values[("snapshot-previous", V2_RANGE)] = [V2_HEADERS, [25, "Khúc bạch tảng", "Topping", "", "", "", "", "10"]]
    with daily_sheet_db.begin() as session:
        session.add_all([
            InventoryDailySheetSnapshotModel(
                tenant_id="tenant-a", business_date=date(2030, 8, 9), external_source_id="source-a",
                source_spreadsheet_file_id="working", snapshot_file_id="snapshot-current", status="completed",
            ),
            InventoryDailySheetSnapshotModel(
                tenant_id="tenant-a", business_date=date(2030, 8, 8), external_source_id="source-a",
                source_spreadsheet_file_id="working", snapshot_file_id="snapshot-previous", status="completed",
            ),
        ])
    result = service(daily_sheet_db, google).reconcile("tenant-a", date(2030, 8, 9), dry_run=True)
    assert result["writes"] == 0
    assert result["changed_count"] == 1
    assert result["variances"][0]["variance"] == "4"
    assert google.update_calls == []


def test_discovery_is_read_only_and_prefills_real_columns(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    google.values[("working", "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!A1:AZ80")] = google.values[("working", V2_RANGE)]
    result = service(daily_sheet_db, google).discover("tenant-a", "working")
    assert result["tabs"][0]["candidate_columns"]["item_key"] == "STT"
    assert result["tabs"][0]["row_counts"]["ITEM"] == 1
    assert result["warnings"][0]["code"] == "workbook_timezone_mismatch"
    assert google.update_calls == []
    assert google.clear_calls == 0

def test_v2_clear_entry_columns_only_targets_item_business_cells(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    worker = service(daily_sheet_db, google)
    worker._reset_v2(google, worker._context("tenant-a"))
    expected = {
        "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!E2",
        "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!F2",
        "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!G2",
        "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!H2",
    }
    assert expected.issubset({key[1] for key in google.values if key[0] == "working"})
    assert "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!A2" not in expected
    assert "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!B2" not in expected
    assert "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!C2" not in expected


def test_v2_carry_forward_is_explicit_and_uses_closing_raw_value(daily_sheet_db):
    configure_v2(daily_sheet_db)
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_config_json = {
            **V2_CONFIG,
            "reset": {"mode": "carry_forward", "clear_columns": ["used", "inbound", "waste", "closing"]},
        }
    google = FakeGoogleV2()
    worker = service(daily_sheet_db, google)
    worker._reset_v2(google, worker._context("tenant-a"))
    updates = [item for call in google.update_calls for item in call]
    assert updates == [{
        "range": "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2",
        "majorDimension": "ROWS",
        "values": [["14"]],
    }]


def test_v2_report_only_run_persists_candidate_and_semantic_trace_without_google_writes(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    google.values[("snapshot-current", V2_RANGE)] = [
        V2_HEADERS, [77, "Completely New Material", "New Category", "", "", "", "", "14"]
    ]
    google.values[("snapshot-previous", V2_RANGE)] = [
        V2_HEADERS, [77, "Completely New Material", "New Category", "", "", "", "", "10"]
    ]
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_automation_enabled = False
        session.add_all([
            InventoryDailySheetSnapshotModel(
                tenant_id="tenant-a", business_date=date(2030, 8, 9),
                external_source_id="source-a", source_spreadsheet_file_id="working",
                snapshot_file_id="snapshot-current", status="completed",
            ),
            InventoryDailySheetSnapshotModel(
                tenant_id="tenant-a", business_date=date(2030, 8, 8),
                external_source_id="source-a", source_spreadsheet_file_id="working",
                snapshot_file_id="snapshot-previous", status="completed",
            ),
        ])
    result = service(daily_sheet_db, google).reconcile(
        "tenant-a", date(2030, 8, 9), dry_run=False
    )
    assert result["status"] == "completed"
    assert result["writes"] == 0
    assert google.update_calls == []
    trace = result["semantic_snapshot"]["current"][0]
    assert trace["sheet"] == "Bảng Kiểm Kê Nguyên Liệu Vật Tư"
    assert trace["spreadsheet_file_id"] == "snapshot-current"
    assert trace["row"] == 2
    assert trace["source_cells"] == ["H2"]
    assert trace["raw_quantity"] == "14"
    assert len(trace["source_hash"]) == 64
    with daily_sheet_db() as session:
        from app.modules.inventory.persistence_model import InventoryMaterialCandidateModel
        candidate = session.scalar(select(InventoryMaterialCandidateModel))
        assert candidate is not None
        assert candidate.sheet == "Bảng Kiểm Kê Nguyên Liệu Vật Tư"
        reconciliation = session.scalar(select(InventoryDailySheetReconciliationModel))
        assert reconciliation.summary_json["writes"] == 0


def test_v2_reset_relevant_schema_drift_is_semantically_explained_but_validation_blocks_reset(daily_sheet_db):
    configure_v2(daily_sheet_db)
    google = FakeGoogleV2()
    drifted_headers = list(V2_HEADERS[:-1])
    drifted_headers.insert(3, V2_HEADERS[-1])
    google.values[("working", V2_RANGE)] = [
        drifted_headers, [25, "Khúc bạch tảng", "Topping", "14", "", "", "", ""]
    ]

    class SemanticAnalyzer:
        def match_material(self, _payload):
            return None
        def analyze_quantity(self, _tenant_id, _payload):
            return None
        def analyze_schema(self, tenant_id, payload):
            assert tenant_id == "tenant-a"
            assert payload["actual_headers"][3] == V2_HEADERS[-1]
            assert payload["layout_drift"] is True
            return {
                "status": "mapped",
                "mapping": dict(V2_CONFIG["source"]["columns"]),
                "confidence": 0.99,
                "requires_review": True,
                "reset_relevant_changed": True,
                "changes": ["closing header renamed and moved"],
            }

    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "token",
        semantic_analyzer=SemanticAnalyzer(),
    )
    report = worker.validate_configuration("tenant-a")
    assert report["valid"] is False
    assert any(error["code"] == "reset_schema_mapping_approval_required" for error in report["errors"])
    with pytest.raises(DailySheetValidationError, match="reset_schema_mapping_approval_required"):
        worker._reset_v2(google, worker._context("tenant-a"))
    assert google.update_calls == []
    assert google.clear_calls == 0


def _configure_carry_forward(sessions):
    configure_v2(sessions)
    with sessions.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_config_json = {
            **V2_CONFIG,
            "reset": {
                "mode": "carry_forward",
                "clear_columns": ["used", "inbound", "waste", "closing"],
            },
        }


def test_v2_carry_forward_verifies_opening_before_clear(daily_sheet_db):
    _configure_carry_forward(daily_sheet_db)

    class RecordingGoogle(FakeGoogleV2):
        def __init__(self):
            super().__init__()
            self.events = []

        def batch_update_values(self, file_id, updates):
            self.events.append("opening_write")
            return super().batch_update_values(file_id, updates)

        def batch_get_values(self, file_id, ranges, **kwargs):
            if ranges == ["'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2"]:
                self.events.append("opening_verify")
            return super().batch_get_values(file_id, ranges, **kwargs)

        def batch_clear_values(self, file_id, ranges):
            self.events.append("clear")
            return super().batch_clear_values(file_id, ranges)

    google = RecordingGoogle()
    worker = service(daily_sheet_db, google)
    worker._reset_v2(google, worker._context("tenant-a"))

    assert google.events.index("opening_write") < google.events.index("opening_verify")
    assert google.events.index("opening_verify") < google.events.index("clear")
    assert google.clear_calls == 1


def test_v2_failed_carry_forward_verification_never_clears(daily_sheet_db):
    _configure_carry_forward(daily_sheet_db)

    class MismatchedGoogle(FakeGoogleV2):
        def batch_get_values(self, file_id, ranges, **kwargs):
            if ranges == ["'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2"]:
                return [{"range": ranges[0], "values": [["wrong value"]]}]
            return super().batch_get_values(file_id, ranges, **kwargs)

    google = MismatchedGoogle()
    worker = service(daily_sheet_db, google)
    with pytest.raises(DailySheetValidationError, match="reset_verification_failed"):
        worker._reset_v2(google, worker._context("tenant-a"))

    assert google.clear_calls == 0
    assert google.values[("working", V2_RANGE)][1][7] == "14"


def test_v2_carry_forward_numeric_google_rendering_is_equivalent(daily_sheet_db):
    _configure_carry_forward(daily_sheet_db)

    class NumericRenderingGoogle(FakeGoogleV2):
        def batch_update_values(self, file_id, updates):
            rendered = [
                {**update, "values": [[int(update["values"][0][0])]]}
                for update in updates
            ]
            return super().batch_update_values(file_id, rendered)

    google = NumericRenderingGoogle()
    worker = service(daily_sheet_db, google)
    worker._reset_v2(google, worker._context("tenant-a"))

    assert google.clear_calls == 1
    assert google.values[("working", "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2")] == [[14]]


def test_v2_carry_forward_structured_raw_text_is_verified_unchanged(daily_sheet_db):
    _configure_carry_forward(daily_sheet_db)
    raw = "1(238,5g); 2(299,4g)"
    google = FakeGoogleV2()
    google.values[("working", V2_RANGE)][1][7] = raw
    worker = service(daily_sheet_db, google)
    worker._reset_v2(google, worker._context("tenant-a"))

    assert google.clear_calls == 1
    assert google.values[("working", "'Bảng Kiểm Kê Nguyên Liệu Vật Tư'!D2")] == [[raw]]


def test_v2_runtime_uses_approved_package_conversion_in_validation_and_reset(daily_sheet_db):
    configure_v2(daily_sheet_db)
    with daily_sheet_db.begin() as session:
        material = InventoryItemModel(
            tenant_id="tenant-a", sku="MATERIAL-25", name="Khúc bạch tảng",
            base_unit="g", preferred_unit="g", canonical_dimension="mass",
        )
        session.add(material)
        session.flush()
        session.add_all([
            InventoryMaterialExternalIdentityModel(
                tenant_id="tenant-a", item_id=material.id, source_type="google_sheet",
                source_id="working", external_key="25", last_seen_name="Khúc bạch tảng",
            ),
            InventoryMaterialPackageConversionModel(
                tenant_id="tenant-a", item_id=material.id,
                package_name="Bich", normalized_package="bich",
                canonical_value=Decimal("1000"), canonical_unit="g", approved_by="admin",
            ),
        ])
    google = FakeGoogleV2()
    google.values[("working", V2_RANGE)][1][7] = "2 bich + 300g"
    worker = service(daily_sheet_db, google)

    assert worker.validate_configuration("tenant-a")["valid"] is True
    worker._reset_v2(google, worker._context("tenant-a"))

    assert google.clear_calls == 1
    with daily_sheet_db() as session:
        assert session.scalar(select(InventoryMaterialCandidateModel)) is None


def test_v2_runtime_semantic_quantity_fallback_is_shared_by_validation_and_reset(daily_sheet_db):
    configure_v2(daily_sheet_db)
    calls = []

    class SemanticAnalyzer:
        def match_material(self, _payload):
            return None

        def analyze_schema(self, _tenant_id, _payload):
            return None

        def analyze_quantity(self, tenant_id, payload):
            calls.append((tenant_id, payload["raw"]))
            return {
                "status": "parsed", "raw": payload["raw"],
                "canonical_value": "700", "canonical_unit": "ml",
                "confidence": 1, "requires_review": False, "warnings": [],
            }

    google = FakeGoogleV2()
    google.values[("working", V2_RANGE)][1][7] = "approximately 700ml"
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "token",
        semantic_analyzer=SemanticAnalyzer(),
    )

    assert worker.validate_configuration("tenant-a")["valid"] is True
    worker._reset_v2(google, worker._context("tenant-a"))

    assert calls == [
        ("tenant-a", "approximately 700ml"),
        ("tenant-a", "approximately 700ml"),
    ]
    assert google.clear_calls == 1


def test_v2_runtime_blank_authoritative_value_never_calls_semantic_or_clears(daily_sheet_db):
    configure_v2(daily_sheet_db)
    calls = []

    class SemanticAnalyzer:
        def match_material(self, _payload):
            return None

        def analyze_schema(self, _tenant_id, _payload):
            return None

        def analyze_quantity(self, tenant_id, payload):
            calls.append((tenant_id, payload))
            return None

    google = FakeGoogleV2()
    google.values[("working", V2_RANGE)][1][7] = ""
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "token",
        semantic_analyzer=SemanticAnalyzer(),
    )

    with pytest.raises(DailyCountSheetValidationError):
        worker._reset_v2(google, worker._context("tenant-a"))

    assert calls == []
    assert google.clear_calls == 0


def test_v2_runtime_structural_hard_block_cannot_be_bypassed_before_reset(daily_sheet_db):
    configure_v2(daily_sheet_db)
    calls = []

    class SemanticAnalyzer:
        def match_material(self, _payload):
            return None

        def analyze_schema(self, _tenant_id, _payload):
            return None

        def analyze_quantity(self, tenant_id, payload):
            calls.append((tenant_id, payload["deterministic_error"]))
            return {
                "status": "parsed", "raw": payload["raw"],
                "canonical_value": "350.5", "canonical_unit": "g",
                "confidence": 1, "requires_review": False, "warnings": [],
            }

    google = FakeGoogleV2()
    google.values[("working", V2_RANGE)][1][3:8] = ["1(350", "5g)", "", "", "10"]
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "token",
        semantic_analyzer=SemanticAnalyzer(),
    )

    with pytest.raises(DailyCountSheetValidationError) as captured:
        worker._reset_v2(google, worker._context("tenant-a"))

    assert "suspected_shifted_quantity" in {error["code"] for error in captured.value.errors}
    assert google.clear_calls == 0


def test_v3_configuration_does_not_require_legacy_archive(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_archive_root_folder_id = None
        settings.daily_sheet_config_json = {
            "version": 3,
            "mode": "gemini_sheet_agent",
            "source": {"sheet": "Daily", "range": "A1:H4"},
        }

    class V3Google(FakeGoogle):
        def drive_file(self, file_id):
            raise AssertionError("V3 validation must not inspect a legacy archive folder")
        def spreadsheet_metadata(self, file_id):
            return {
                "properties": {"title": "Daily", "timeZone": "Asia/Ho_Chi_Minh"},
                "sheets": [{"properties": {"title": "Daily", "sheetId": 1}}],
            }

    google = V3Google()
    google.values[("working", "'Daily'!A1:H4")] = [["STT", "Material"], ["1", "Milk"]]
    report = service(daily_sheet_db, google).validate_configuration("tenant-a")
    assert report["valid"] is True
    assert {"code": "gemini_sheet_agent_source", "ok": True} in report["checks"]


V4_CONFIG = {
    "version": 4,
    "mode": "gemini_tool_sheet_agent",
    "source": {
        "spreadsheet_file_id": "working",
        "allowed_sheets": ["Arbitrary"],
    },
}


def configure_v4(
    sessions,
    *,
    spreadsheet_file_id="working",
    allowed_sheets=("Arbitrary",),
    automation_enabled=False,
    stale_template=False,
):
    with sessions.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_automation_enabled = automation_enabled
        settings.daily_archive_root_folder_id = None
        settings.daily_template_spreadsheet_file_id = (
            "stale-legacy-template" if stale_template else None
        )
        settings.daily_sheet_config_json = {
            **V4_CONFIG,
            "source": {
                "spreadsheet_file_id": spreadsheet_file_id,
                "allowed_sheets": list(allowed_sheets),
            },
        }


class V4ValidationGoogle(FakeGoogle):
    def __init__(self, *, sheets=("Arbitrary",)):
        super().__init__()
        self.sheets = tuple(sheets)
        self.validated_file_ids = []
        self.metadata_calls = []

    def validate_native_spreadsheet(self, file_id):
        self.validated_file_ids.append(file_id)
        return super().validate_native_spreadsheet(file_id)

    def drive_file(self, _file_id):
        raise AssertionError("V4 validation must not inspect a legacy archive")

    def spreadsheet_metadata(self, file_id):
        self.metadata_calls.append(file_id)
        return {
            "properties": {
                "title": "V4 workbook",
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "sheets": [
                {"properties": {"title": title, "sheetId": index + 1}}
                for index, title in enumerate(self.sheets)
            ],
        }

    def batch_get_values(self, *_args, **_kwargs):
        raise AssertionError("V4 validation must not read legacy source/template ranges")


def test_v4_validation_requires_only_working_sheet_and_allowed_tabs(daily_sheet_db):
    configure_v4(daily_sheet_db)
    google = V4ValidationGoogle()

    with patch(
        "app.modules.inventory.daily_sheet.service.parse_stock_records",
        side_effect=AssertionError("legacy parser called"),
    ), patch.object(
        InventoryDailySheetService,
        "_parse_v2_runtime",
        side_effect=AssertionError("V2 parser called"),
    ):
        report = service(daily_sheet_db, google).validate_configuration("tenant-a")

    assert report["valid"] is True
    assert report["errors"] == []
    assert google.validated_file_ids == ["working"]
    assert google.metadata_calls == ["working"]
    assert {"code": "gemini_tool_sheet_agent_metadata", "ok": True} in report["checks"]


def test_v4_validation_reports_missing_allowed_sheet(daily_sheet_db):
    configure_v4(daily_sheet_db, allowed_sheets=("Arbitrary", "Missing"))
    report = service(
        daily_sheet_db,
        V4ValidationGoogle(sheets=("Arbitrary",)),
    ).validate_configuration("tenant-a")

    assert report["valid"] is False
    assert {"code": "configured_sheet_missing", "sheet": "Missing"} in report["errors"]


def test_v4_validation_rejects_different_configured_spreadsheet(daily_sheet_db):
    configure_v4(daily_sheet_db, spreadsheet_file_id="different-working-file")
    report = service(
        daily_sheet_db,
        V4ValidationGoogle(),
    ).validate_configuration("tenant-a")

    assert report["valid"] is False
    assert {"code": "spreadsheet_not_authorized"} in report["errors"]


def test_v4_validation_ignores_stale_legacy_template(daily_sheet_db):
    configure_v4(daily_sheet_db, stale_template=True)
    google = V4ValidationGoogle()
    report = service(daily_sheet_db, google).validate_configuration("tenant-a")

    assert report["valid"] is True
    assert google.validated_file_ids == ["working"]


def test_v4_manual_shadow_runs_while_daily_automation_is_disabled(daily_sheet_db):
    configure_v4(daily_sheet_db, automation_enabled=False)

    class AgentV4:
        def __init__(self):
            self.calls = []

        def run_shadow(self, tenant_id, business_date):
            self.calls.append((tenant_id, business_date))
            return SimpleNamespace(status="shadow", writes=0)

    agent = AgentV4()
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: (_ for _ in ()).throw(
            AssertionError("legacy Google validation called")
        ),
        token_resolver=lambda _connection: "token",
        agent_v4_service=agent,
    )

    result = worker.run_agent_v4_shadow("tenant-a", date(2030, 8, 9))

    assert result.status == "shadow"
    assert result.writes == 0
    assert agent.calls == [("tenant-a", date(2030, 8, 9))]


def test_v4_snapshot_rejects_manual_shadow_mode_and_reconcile_is_report_only(
    daily_sheet_db,
):
    configure_v4(daily_sheet_db, automation_enabled=True)
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: (_ for _ in ()).throw(
            AssertionError("legacy Google flow called")
        ),
        token_resolver=lambda _connection: "token",
    )

    with pytest.raises(DailySheetConfigurationError, match="manual shadow-only"):
        worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))

    context = worker._context("tenant-a")
    with pytest.raises(DailySheetConfigurationError, match="manual shadow-only"):
        worker._source_ranges(context.config)
    with pytest.raises(DailySheetConfigurationError, match="manual shadow-only"):
        worker._reset_and_verify(None, context)

    assert worker.reconcile("tenant-a", date(2030, 8, 9)) == {
        "status": "report_only",
        "business_date": "2030-08-09",
        "writes": 0,
    }


def test_snapshot_and_reset_routes_v3_to_injected_agent_without_legacy_snapshot(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_archive_root_folder_id = None
        settings.daily_sheet_config_json = {
            "version": 3,
            "mode": "gemini_sheet_agent",
            "source": {"sheet": "Daily", "range": "A1:H4"},
            "agent": {"apply_mode": "shadow"},
        }

    class Agent:
        def __init__(self):
            self.calls = []
        def plan_agent_run(self, tenant_id, business_date, *, dry_run):
            self.calls.append((tenant_id, business_date, dry_run))
            return SimpleNamespace(status="shadow")

    agent = Agent()
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: (_ for _ in ()).throw(AssertionError("legacy Google flow called")),
        token_resolver=lambda _connection: "token",
        agent_service=agent,
    )
    result = worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))
    assert result.status == "shadow"
    assert agent.calls == [("tenant-a", date(2030, 8, 9), True)]


def test_manual_v3_plan_works_while_daily_automation_is_disabled(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_automation_enabled = False
        settings.daily_archive_root_folder_id = None
        settings.daily_sheet_config_json = {
            "version": 3,
            "mode": "gemini_sheet_agent",
            "source": {"sheet": "Daily", "range": "A1:H4"},
            "agent": {"apply_mode": "shadow"},
        }

    class Agent:
        def __init__(self):
            self.calls = []

        def plan_agent_run(self, tenant_id, business_date, *, dry_run):
            self.calls.append((tenant_id, business_date, dry_run))
            return SimpleNamespace(status="shadow")

    agent = Agent()
    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: (_ for _ in ()).throw(
            AssertionError("manual planning must not enter the legacy Google flow")
        ),
        token_resolver=lambda _connection: "token",
    )

    def build_agent_service(**kwargs):
        manual_context = kwargs["context_provider"]("tenant-a")
        assert manual_context.config.agent.apply_mode == "shadow"
        return agent

    with patch(
        "app.modules.inventory.daily_sheet.agent.service.build_daily_sheet_agent_service",
        side_effect=build_agent_service,
    ):
        result = worker.plan_agent_run("tenant-a", date(2030, 8, 9), dry_run=True)

    assert result.status == "shadow"
    assert worker.is_agent_v3_configured("tenant-a") is True
    assert agent.calls == [("tenant-a", date(2030, 8, 9), True)]


def test_scheduler_oriented_snapshot_still_requires_daily_automation_enabled(daily_sheet_db):
    with daily_sheet_db.begin() as session:
        settings = session.scalar(select(InventorySettingsModel))
        settings.daily_sheet_automation_enabled = False
        settings.daily_archive_root_folder_id = None
        settings.daily_sheet_config_json = {
            "version": 3,
            "mode": "gemini_sheet_agent",
            "source": {"sheet": "Daily", "range": "A1:H4"},
            "agent": {"apply_mode": "shadow"},
        }

    class Agent:
        def plan_agent_run(self, *_args, **_kwargs):
            raise AssertionError("disabled scheduler must not plan")

    worker = InventoryDailySheetService(
        daily_sheet_db,
        client_factory=lambda _token: FakeGoogle(),
        token_resolver=lambda _connection: "token",
        agent_service=Agent(),
    )

    with pytest.raises(DailySheetConfigurationError, match="automation is disabled"):
        worker.snapshot_and_reset("tenant-a", date(2030, 8, 9))
