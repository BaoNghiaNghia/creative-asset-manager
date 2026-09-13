from datetime import date
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.carry_forward import (
    CarryForwardPlan,
    InventorySharedCarryForwardService,
)
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventoryDailySheetSnapshotModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventorySettingsModel,
)


class Google:
    def __init__(self, source_values=None, target_values=None, protected=False):
        self.source_values = source_values or {"H14": 50, "H15": 35, "H16": 25}
        self.target_values = target_values or {"B14": 1, "B15": 1, "B16": 1}
        self.writes = []
        self.protected = protected

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def validate_native_spreadsheet(self, file_id):
        return {"id": file_id, "mimeType": "application/vnd.google-apps.spreadsheet", "capabilities": {"canEdit": file_id == "shared"}}
    def spreadsheet_metadata(self, _file_id):
        sheets = []
        for index, title in enumerate(["One", "Two", "Three", "Warehouses"]):
            item = {"properties": {"sheetId": index + 1, "title": title}}
            if index == 3 and self.protected:
                item["protectedRanges"] = [{"range": {"sheetId": 4, "startRowIndex": 13, "endRowIndex": 14, "startColumnIndex": 1, "endColumnIndex": 2}}]
            sheets.append(item)
        return {"sheets": sheets}
    def batch_get_values(self, file_id, ranges, *, value_render_option="UNFORMATTED_VALUE"):
        result = []
        for value in ranges:
            cell = value.rsplit("!", 1)[-1]
            values = self.source_values if file_id == "gemini" else self.target_values
            raw = "" if value_render_option == "FORMULA" else values.get(cell)
            result.append({"range": value, "values": [] if raw is None else [[raw]]})
        return result
    def batch_update_values(self, file_id, updates):
        assert file_id == "shared"
        self.writes.extend(updates)
        for update in updates:
            self.target_values[update["range"].rsplit("!", 1)[-1]] = update["values"][0][0]


class Planner:
    def __init__(self, rows): self.rows = rows
    def plan(self, **_kwargs):
        return CarryForwardPlan(self.rows, [], {"sheetId": 4, "title": "Warehouses", "index": 3})


def row(material_id, warehouse_id, source_cell, target_cell, closing, target_before):
    return {
        "material_id": material_id, "warehouse_id": warehouse_id,
        "source": {"spreadsheet_file_id": "gemini", "sheet": "Warehouses", "cell": source_cell, "closing_value": closing, "evidence_hash": canonical_hash([closing])},
        "target": {"spreadsheet_file_id": "shared", "sheet": "Warehouses", "cell": target_cell, "opening_value": closing, "evidence_hash": canonical_hash([target_before])},
    }


def make_db():
    temp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temp.name) / 'carry.db'}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants", "oauth_connections", "external_sources", "inventory_settings", "inventory_daily_sheet_snapshots", "inventory_daily_carry_forwards", "inventory_items", "inventory_locations"):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")])
        session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive", source_metadata={"oauth_connection_id": "connection"}))
        session.add(InventorySettingsModel(tenant_id="tenant-a", external_source_id="source-a", inbox_folder_id="inbox", enabled=True, daily_sheet_automation_enabled=True, daily_working_spreadsheet_file_id="shared", daily_sheet_config_json={"version": 4}))
        session.add(InventoryDailySheetSnapshotModel(id="snapshot-a", tenant_id="tenant-a", business_date=date(2030, 8, 9), external_source_id="source-a", source_spreadsheet_file_id="shared", snapshot_file_id="snapshot", gemini_file_id="gemini", status="completed"))
        for number, value in enumerate(("a", "b", "c"), 1):
            session.add(InventoryItemModel(id=f"material-{value}", tenant_id="tenant-a", sku=value, name=value, base_unit="unit"))
            session.add(InventoryLocationModel(id=f"warehouse-{value}", tenant_id="tenant-a", code=value, name=value))
    return temp, engine, sessions


def test_carry_forward_preserves_each_warehouse_and_writes_shared_only():
    temp, engine, sessions = make_db()
    google = Google()
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1), row("material-b", "warehouse-b", "H15", "B15", 35, 1), row("material-c", "warehouse-c", "H16", "B16", 25, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "completed"
    assert result.idempotency_key == "inventory-shared-carry-forward:v1:tenant-a:2030-08-10"
    assert google.target_values == {"B14": 50, "B15": 35, "B16": 25}
    assert len(google.writes) == 3
    engine.dispose(); temp.cleanup()


def test_zero_is_carried_but_blank_requires_review_without_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 0, "H15": None}, target_values={"B14": 1, "B15": 1})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 0, 1), row("material-b", "warehouse-b", "H15", "B15", "", 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_already_applied_recovery_does_not_duplicate_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 50})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 50)]
    service = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows))
    assert service.run("tenant-a", date(2030, 8, 10)).status == "completed"
    assert google.writes == []
    assert service.run("tenant-a", date(2030, 8, 10)).status == "completed"
    engine.dispose(); temp.cleanup()


def test_cross_tenant_material_is_rejected_before_shared_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    rows = [row("material-x", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_protected_or_formula_target_is_rejected_before_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1}, protected=True)
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_read_back_mismatch_is_not_completed():
    temp, engine, sessions = make_db()
    class NoopWriterGoogle(Google):
        def batch_update_values(self, file_id, updates):
            assert file_id == "shared"
            self.writes.extend(updates)
    google = NoopWriterGoogle(source_values={"H14": 50}, target_values={"B14": 1})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "retryable_failure"
    assert result.error_code == "stale_evidence"
    engine.dispose(); temp.cleanup()
