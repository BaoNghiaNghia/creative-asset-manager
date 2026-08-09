from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.persistence_model import InventoryDocumentModel, InventorySettingsModel
from app.modules.inventory.repository import (
    InventoryCatalogRepository,
    InventoryDailyRepository,
    InventoryDocumentRepository,
    InventoryLedgerRepository,
    InventoryReviewRepository,
    InventorySettingsRepository,
    InventorySourceFileRepository,
    InventoryTenantRepository,
)
from app.modules.inventory.schema import (
    InventoryAnalysisInput,
    InventoryDailyRunInput,
    InventoryDocumentInput,
    InventoryDocumentPageInput,
    InventoryExportInput,
    InventoryItemInput,
    InventoryLineInput,
    InventoryReviewInput,
    InventorySettingsInput,
    InventorySourceFileInput,
    InventoryTransactionInput,
)
from tests.modules.inventory.test_persistence import PHASE2_TABLES


NOW = datetime(2030, 8, 9, 8, 0, tzinfo=timezone.utc)


class InventoryPersistenceRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'inventory-phase2.db'}"
        )
        event.listen(
            self.engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        selected = PHASE2_TABLES | {"tenants", "external_sources"}
        for table in Base.metadata.sorted_tables:
            if table.name in selected:
                table.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all(
                [
                    TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"),
                    TenantModel(id="tenant-b", name="Tenant B", slug="tenant-b"),
                    ExternalSourceModel(
                        id="source-a", tenant_id="tenant-a", source_key="drive-a",
                        source_type="google_drive",
                    ),
                    ExternalSourceModel(
                        id="source-b", tenant_id="tenant-b", source_key="drive-b",
                        source_type="google_drive",
                    ),
                ]
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    @staticmethod
    def source(source_id: str, file_id: str, digest: str | None = None):
        return InventorySourceFileInput(
            external_source_id=source_id,
            drive_file_id=file_id,
            filename=f"{file_id}.jpg",
            mime_type="image/jpeg",
            drive_modified_time=NOW,
            content_sha256=digest,
        )

    def graph(self, session):
        catalog = InventoryCatalogRepository(session)
        location = catalog.create_location("tenant-a", code="MAIN", name="Main")
        item = catalog.create_item(
            "tenant-a", InventoryItemInput(sku="SKU", name="Item", base_unit="piece")
        )
        source = InventorySourceFileRepository(session).register(
            "tenant-a", self.source("source-a", "file")
        )
        documents = InventoryDocumentRepository(session)
        document = documents.create_document(
            "tenant-a",
            InventoryDocumentInput(
                idempotency_key="document", business_date=date(2030, 8, 9),
                document_type="stock_count", location_id=location.id,
            ),
        )
        page = documents.add_page(
            "tenant-a",
            InventoryDocumentPageInput(
                document_id=document.id, source_file_id=source.id,
                drive_file_id="file", page_number=1,
            ),
        )
        analysis = documents.record_analysis(
            "tenant-a",
            InventoryAnalysisInput(
                document_id=document.id, page_id=page.id, analysis_version=1,
                idempotency_key="analysis", provider="gemini", model="model",
                prompt_version="p1", schema_version="s1",
            ),
        )
        line = documents.add_line(
            "tenant-a",
            InventoryLineInput(
                document_id=document.id, page_id=page.id, analysis_id=analysis.id,
                line_number=1, raw_item_name="Item", item_id=item.id,
                quantity_base_unit=Decimal("2"), confidence=Decimal("0.9"),
            ),
        )
        return location, item, document, line

    def test_cross_tenant_reads_and_business_keys_are_scoped(self) -> None:
        with self.sessions() as session:
            settings = InventorySettingsRepository(session).upsert(
                "tenant-a",
                InventorySettingsInput(
                    external_source_id="source-a", inbox_folder_id="inbox"
                ),
            )
            InventorySettingsRepository(session).upsert(
                "tenant-b",
                InventorySettingsInput(
                    external_source_id="source-b", inbox_folder_id="inbox"
                ),
            )
            self.assertIsNone(
                InventoryTenantRepository(session).get(
                    InventorySettingsModel, "tenant-b", settings.id
                )
            )
            first = InventoryCatalogRepository(session).create_location(
                "tenant-a", code="MAIN", name="A"
            )
            second = InventoryCatalogRepository(session).create_location(
                "tenant-b", code="MAIN", name="B"
            )
            self.assertNotEqual(first.id, second.id)

    def test_provider_version_and_content_hash_idempotency(self) -> None:
        with self.sessions() as session:
            repository = InventorySourceFileRepository(session)
            digest = "a" * 64
            first = repository.register(
                "tenant-a", self.source("source-a", "file-a", digest)
            )
            repeated = repository.register(
                "tenant-a", self.source("source-a", "file-a", digest)
            )
            second = repository.register(
                "tenant-a", self.source("source-a", "file-b", digest)
            )
            other = repository.register(
                "tenant-b", self.source("source-b", "file-a", digest)
            )
            self.assertEqual(first.id, repeated.id)
            self.assertEqual(
                {first.id, second.id},
                {row.id for row in repository.find_by_content_hash("tenant-a", digest)},
            )
            self.assertNotEqual(first.id, other.id)

    def test_database_rejects_cross_tenant_document_relationship(self) -> None:
        with self.sessions() as session:
            location, _item, _document, _line = self.graph(session)
            session.add(
                InventoryDocumentModel(
                    tenant_id="tenant-b", idempotency_key="cross-tenant",
                    business_date=date(2030, 8, 9), document_type="stock_count",
                    location_id=location.id,
                )
            )
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_review_ledger_daily_run_and_export_are_idempotent(self) -> None:
        with self.sessions() as session:
            location, item, document, line = self.graph(session)
            reviews = InventoryReviewRepository(session)
            review = InventoryReviewInput(
                document_id=document.id, line_id=line.id,
                idempotency_key="review", reason_code="low_confidence",
            )
            self.assertEqual(
                reviews.create("tenant-a", review).id,
                reviews.create("tenant-a", review).id,
            )
            ledger = InventoryLedgerRepository(session)
            transaction = InventoryTransactionInput(
                idempotency_key="transaction", business_date=date(2030, 8, 9),
                location_id=location.id, item_id=item.id,
                transaction_type="closing_count", quantity_base_unit=Decimal("2"),
                base_unit_snapshot="piece", conversion_factor_snapshot=Decimal("1"),
                source_document_id=document.id, source_line_id=line.id,
            )
            first = ledger.append("tenant-a", transaction)
            self.assertEqual(first.id, ledger.append("tenant-a", transaction).id)
            self.assertEqual(
                [first.id],
                [
                    row.id
                    for row in ledger.history(
                        "tenant-a", location_id=location.id, item_id=item.id
                    )
                ],
            )
            daily = InventoryDailyRepository(session)
            run_value = InventoryDailyRunInput(
                business_date=date(2030, 8, 9), idempotency_key="run"
            )
            run = daily.get_or_create_run("tenant-a", run_value)
            self.assertEqual(run.id, daily.get_or_create_run("tenant-a", run_value).id)
            export_value = InventoryExportInput(
                daily_run_id=run.id, idempotency_key="export", export_version=1,
                period_month="2030-08", business_date=date(2030, 8, 9),
            )
            export = daily.create_export("tenant-a", export_value)
            self.assertEqual(export.id, daily.create_export("tenant-a", export_value).id)
