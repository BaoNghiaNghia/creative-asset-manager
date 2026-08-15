from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel  # noqa: F401
from app.modules.inventory.daily.report import DailyReportNotFinalized, InventoryDailyReportService
from app.modules.inventory.persistence_model import (
    InventoryDailyRunModel,
    InventoryDocumentModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventoryReviewModel,
    InventoryTransactionModel,
)


DAY = date(2030, 8, 9)


class DailyReportPhase9Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'report.db'}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add_all((TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")))
        self.service = InventoryDailyReportService(self.sessions)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def _seed(self, *, finalized: bool = True) -> None:
        with self.sessions.begin() as session:
            location = InventoryLocationModel(id="location-a", tenant_id="tenant-a", code="BAR", name="Bar")
            item = InventoryItemModel(id="item-a", tenant_id="tenant-a", sku="BEAN", name="Beans", base_unit="kg")
            document = InventoryDocumentModel(id="document-a", tenant_id="tenant-a", idempotency_key="document-a", business_date=DAY, document_type="stock_count", location_id="location-a", status="finalized")
            run = InventoryDailyRunModel(id="run-a", tenant_id="tenant-a", business_date=DAY, idempotency_key="run-a", status="finalized" if finalized else "ready", finalized_at=datetime(2030, 8, 9, 10, tzinfo=timezone.utc) if finalized else None, finalized_by="manager", finalized_with_missing=True, location_state_json={"ready": True, "blockers": []})
            session.add_all((location, item, document, run, InventoryReviewModel(tenant_id="tenant-a", document_id="document-a", idempotency_key="review-a", reason_code="check", status="pending")))
            for kind, quantity in (("opening_balance", "10.12500000"), ("receipt", "1.00000000"), ("transfer_in", "2.00000000"), ("transfer_out", "1.00000000"), ("closing_count", "15.00000000"), ("waste", "0.50000000"), ("usage_adjustment", "0.25000000"), ("receipt", "9.00000000")):
                status = "reversed" if quantity == "9.00000000" else "posted"
                session.add(InventoryTransactionModel(tenant_id="tenant-a", idempotency_key=f"tx-{kind}-{quantity}", business_date=DAY, location_id="location-a", item_id="item-a", transaction_type=kind, quantity_base_unit=Decimal(quantity), base_unit_snapshot="kg", conversion_factor_snapshot=Decimal("1"), source_document_id="document-a", status=status))

    def test_requires_finalized_day(self) -> None:
        self._seed(finalized=False)
        with self.assertRaises(DailyReportNotFinalized):
            self.service.generate("tenant-a", DAY)

    def test_snapshot_is_tenant_scoped_idempotent_and_exact(self) -> None:
        self._seed()
        first = self.service.generate("tenant-a", DAY)
        second = self.service.generate("tenant-a", DAY)
        self.assertEqual(first, second)
        self.assertIsNone(self.service.get("tenant-b", DAY))
        self.assertEqual("inventory-daily-report-v1", first["schema_version"])
        self.assertTrue(first["daily_run"]["forced"])
        self.assertEqual({"finalized": 1}, first["documents"]["by_status"])
        self.assertEqual(1, first["reviews"]["unresolved"])
        self.assertEqual(7, first["transactions"]["total"])
        row = first["locations"][0]["items"][0]
        self.assertEqual("-3.37500000", row["usage"])
        self.assertEqual("0.25000000", row["usage_adjustment"])
        self.assertEqual("10.12500000", row["opening"])
        self.assertEqual("negative_usage", first["anomalies"][0]["code"])



if __name__ == "__main__":
    unittest.main()
