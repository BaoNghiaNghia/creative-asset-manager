from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily_sheet.agent_v4.tools import V4WorkbookToolHost
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventoryItemAliasModel,
    InventoryItemModel,
)


def _sessions(table_names: tuple[str, ...]):
    temp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temp.name) / 'db.sqlite'}")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    for name in table_names:
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
    return temp, engine, sessions


def test_lifecycle_history_pages_dates_in_sql_without_loading_full_history():
    temp, engine, sessions = _sessions(
        (
            "tenants",
            "inventory_settings",
            "inventory_daily_sheet_snapshots",
            "inventory_daily_carry_forwards",
            "inventory_jobs",
        )
    )
    start = date(2030, 1, 1)
    with sessions.begin() as session:
        for index in range(60):
            day = start + timedelta(days=index)
            session.add(
                InventoryDailyCarryForwardModel(
                    tenant_id="tenant-a",
                    target_business_date=day,
                    previous_business_date=day - timedelta(days=1),
                    idempotency_key=f"carry-{day.isoformat()}",
                    warehouse_sheet_identity_json={},
                    plan_json={},
                    status="completed",
                )
            )
    service = InventoryDailySheetService(
        sessions,
        clock=lambda: datetime(2030, 3, 15, tzinfo=timezone.utc),
    )
    page = service.lifecycle_history("tenant-a", page=2, page_size=25)
    ordered = sorted((start + timedelta(days=i) for i in range(60)), reverse=True)
    assert page["total"] == 60
    assert page["pages"] == 3
    assert [item["business_date"] for item in page["items"]] == [
        day.isoformat() for day in ordered[25:50]
    ]
    engine.dispose()
    temp.cleanup()


def test_material_catalog_search_is_bounded_and_matches_aliases():
    temp, engine, sessions = _sessions(
        ("tenants", "inventory_items", "inventory_item_aliases")
    )
    with sessions.begin() as session:
        session.add_all(
            [
                InventoryItemModel(
                    id="material-a",
                    tenant_id="tenant-a",
                    sku="MAT-001",
                    name="Material One",
                    base_unit="kg",
                    category="fabric",
                ),
                InventoryItemModel(
                    id="material-b",
                    tenant_id="tenant-a",
                    sku="MAT-002",
                    name="Other Material",
                    base_unit="kg",
                    category="misc",
                ),
            ]
        )
        session.flush()
        session.add(
            InventoryItemAliasModel(
                id="alias-a",
                tenant_id="tenant-a",
                item_id="material-a",
                alias="Cotton old",
                normalized_alias="cotton old",
            )
        )

    host = V4WorkbookToolHost(
        tenant_id="tenant-a",
        spreadsheet_file_id="unused",
        allowed_sheets=[],
        google=object(),
        session_factory=sessions,
        max_read_calls=10,
        max_read_cells=100,
        max_edit_operations=10,
    )
    result = host.search_material_catalog({"query": "cotton", "limit": 10})
    assert [item["material_id"] for item in result["materials"]] == ["material-a"]
    assert result["truncated"] is False

    bounded = host.search_material_catalog({"limit": 1})
    assert len(bounded["materials"]) == 1
    assert bounded["limit"] == 1
    assert bounded["truncated"] is True
    engine.dispose()
    temp.cleanup()
