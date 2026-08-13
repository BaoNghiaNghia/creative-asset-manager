from __future__ import annotations

import unittest
from pathlib import Path

from app.core.database import Base
from app.modules.inventory import persistence_model  # noqa: F401


PHASE2_TABLES = {
    "inventory_settings",
    "inventory_source_files",
    "inventory_locations",
    "inventory_items",
    "inventory_item_aliases",
    "inventory_documents",
    "inventory_document_pages",
    "inventory_ai_analyses",
    "inventory_lines",
    "inventory_reviews",
    "inventory_transactions",
    "inventory_daily_runs",
    "inventory_exports",
}


class InventoryPersistenceMetadataTest(unittest.TestCase):
    def test_phase2_defines_exactly_thirteen_business_tables(self) -> None:
        inventory_tables = {
            name
            for name in Base.metadata.tables
            if name.startswith("inventory_")
            and name not in {"inventory_jobs", "inventory_processing_controls", "inventory_ai_controls", "inventory_review_events", "inventory_daily_run_events", "inventory_ai_credentials", "inventory_ai_credential_audits"}
        }
        self.assertEqual(inventory_tables, PHASE2_TABLES)
        for table_name in PHASE2_TABLES:
            self.assertIn("tenant_id", Base.metadata.tables[table_name].c)

    def test_foreign_keys_do_not_target_creative_business_tables(self) -> None:
        allowed = PHASE2_TABLES | {"tenants", "external_sources"}
        for table_name in PHASE2_TABLES:
            for foreign_key in Base.metadata.tables[table_name].foreign_keys:
                self.assertIn(foreign_key.column.table.name, allowed)

    def test_migration_only_changes_phase2_inventory_tables(self) -> None:
        migration = (
            Path(__file__).resolve().parents[5]
            / "database/migrations/versions/a7dd7ccdbf1a_add_inventory_persistence_models.py"
        ).read_text()
        self.assertIn("0033_inventory_isolation", migration)
        for table_name in PHASE2_TABLES:
            self.assertIn(f"op.create_table('{table_name}'", migration)
        for forbidden in (
            "asset_pipelines",
            "ai_batch_jobs",
            "search_index_records",
            "source_assets",
        ):
            self.assertNotIn(forbidden, migration)
