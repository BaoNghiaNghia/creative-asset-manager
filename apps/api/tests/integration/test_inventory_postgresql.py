from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.persistence_model import InventoryDocumentModel
from app.modules.inventory.repository import InventoryCatalogRepository, InventorySourceFileRepository
from app.modules.inventory.schema import InventorySourceFileInput
from tests.modules.inventory.test_persistence import PHASE2_TABLES


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class InventoryPostgreSqlIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("Inventory integration tests require PostgreSQL")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_phase2_tables_exist_after_migration(self) -> None:
        self.assertTrue(PHASE2_TABLES <= set(inspect(self.engine).get_table_names()))

    def test_provider_identity_and_cross_tenant_fk_on_postgresql(self) -> None:
        marker = uuid4().hex
        tenant_a, tenant_b = f"inv-a-{marker}", f"inv-b-{marker}"
        source_a, source_b = f"sa-{marker}", f"sb-{marker}"
        with self.sessions() as session:
            session.add_all(
                [
                    TenantModel(id=tenant_a, name="A", slug=tenant_a),
                    TenantModel(id=tenant_b, name="B", slug=tenant_b),
                    ExternalSourceModel(id=source_a, tenant_id=tenant_a, source_key=source_a, source_type="google_drive"),
                    ExternalSourceModel(id=source_b, tenant_id=tenant_b, source_key=source_b, source_type="google_drive"),
                ]
            )
            session.commit()
            catalog = InventoryCatalogRepository(session)
            location_a = catalog.create_location(tenant_a, code="MAIN", name="A")
            location_b = catalog.create_location(tenant_b, code="MAIN", name="B")
            value = InventorySourceFileInput(
                external_source_id=source_a, drive_file_id="same-file",
                filename="count.jpg", mime_type="image/jpeg",
                drive_modified_time=datetime.now(timezone.utc),
            )
            files = InventorySourceFileRepository(session)
            file_a = files.register(tenant_a, value)
            file_b = files.register(
                tenant_b, value.model_copy(update={"external_source_id": source_b})
            )
            session.commit()
            self.assertNotEqual(location_a.id, location_b.id)
            self.assertNotEqual(file_a.id, file_b.id)
            self.assertEqual(file_a.id, files.register(tenant_a, value).id)

        with self.sessions() as session:
            session.add(
                InventoryDocumentModel(
                    tenant_id=tenant_b, idempotency_key=f"cross-{marker}",
                    business_date=date.today(), document_type="stock_count",
                    location_id=location_a.id,
                )
            )
            with self.assertRaises(IntegrityError):
                session.flush()
