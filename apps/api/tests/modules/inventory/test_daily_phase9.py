from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily.service import DailyRunBlocked, InventoryDailyRunService
from app.modules.inventory.persistence_model import (
    InventoryDailyRunEventModel,
    InventoryDocumentModel,
)

DAY = date(2030, 8, 9)


class InventoryDailyRunServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'daily.db'}")
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        for table in Base.metadata.sorted_tables:
            if table.name in {"tenants", "external_sources", "inventory_locations", "inventory_documents", "inventory_daily_runs", "inventory_daily_run_events", "inventory_reviews", "inventory_transactions", "inventory_jobs"}:
                table.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(TenantModel(id="tenant-a", name="A", slug="a"))
        self.service = InventoryDailyRunService(self.sessions)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_missing_documents_blocks_and_checkpoint_is_idempotent(self) -> None:
        first = self.service.evaluate("tenant-a", DAY)
        second = self.service.evaluate("tenant-a", DAY)
        self.assertFalse(first.ready)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.snapshot["blockers"][0]["code"], "missing_documents")
        with self.sessions() as session:
            self.assertEqual(1, len(list(session.scalars(select(InventoryDailyRunEventModel)))))

    def test_manual_finalize_blocks_then_force_finalizes_with_audit(self) -> None:
        self.service.evaluate("tenant-a", DAY)
        with self.assertRaises(DailyRunBlocked):
            self.service.finalize("tenant-a", DAY, actor_id="reviewer")
        with self.assertRaisesRegex(ValueError, "forced_finalize_reason_required"):
            self.service.finalize("tenant-a", DAY, actor_id="reviewer", force=True)
        result = self.service.finalize("tenant-a", DAY, actor_id="reviewer", force=True, reason="Operations exception")
        repeated = self.service.finalize("tenant-a", DAY, actor_id="other", force=True, reason="ignored")
        self.assertTrue(result.finalized)
        self.assertTrue(result.forced)
        self.assertEqual(result.id, repeated.id)
        with self.sessions() as session:
            events = list(session.scalars(select(InventoryDailyRunEventModel).order_by(InventoryDailyRunEventModel.created_at)))
        self.assertEqual(["completeness_check", "forced_finalized"], [row.event_type for row in events])
        self.assertEqual("reviewer", events[-1].actor_id)
        self.assertEqual("Operations exception", events[-1].reason)

    def test_uncommitted_approved_document_blocks_finalization(self) -> None:
        with self.sessions.begin() as session:
            session.add(InventoryDocumentModel(
                tenant_id="tenant-a",
                idempotency_key="doc-a",
                business_date=DAY,
                document_type="stock_count",
                status="approved",
                expected_pages=0,
                received_pages=0,
            ))
        result = self.service.evaluate("tenant-a", DAY)
        self.assertFalse(result.ready)
        self.assertEqual("uncommitted_approved_documents", result.snapshot["blockers"][0]["code"])

    def test_open_review_and_needs_reupload_block_normal_finalize(self) -> None:
        with self.sessions.begin() as session:
            session.add(InventoryDocumentModel(tenant_id="tenant-a", idempotency_key="reupload", business_date=DAY, document_type="stock_count", status="needs_reupload", expected_pages=1, received_pages=1))
        result = self.service.evaluate("tenant-a", DAY, checkpoint="preclose_check")
        self.assertFalse(result.ready)
        self.assertEqual("document_not_ready", result.snapshot["blockers"][0]["code"])
        with self.assertRaises(DailyRunBlocked):
            self.service.finalize("tenant-a", DAY, actor_id="reviewer")

    def test_business_date_uses_asia_ho_chi_minh(self) -> None:
        self.assertEqual(DAY, self.service.business_date(datetime(2030, 8, 8, 17, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
