from __future__ import annotations

import unittest

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.core.database import Base
from app.modules.inventory import persistence_model  # noqa: F401


def unique_columns(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, UniqueConstraint)
    }


class InventoryPersistenceConstraintTest(unittest.TestCase):
    def test_alias_and_analysis_versions_are_tenant_scoped(self) -> None:
        self.assertIn(
            ("tenant_id", "normalized_alias"),
            unique_columns("inventory_item_aliases"),
        )
        self.assertIn(
            ("tenant_id", "page_id", "analysis_version"),
            unique_columns("inventory_ai_analyses"),
        )

    def test_transactions_daily_runs_and_exports_have_tenant_idempotency(self) -> None:
        self.assertIn(
            ("tenant_id", "idempotency_key"),
            unique_columns("inventory_transactions"),
        )
        self.assertIn(
            ("tenant_id", "business_date"),
            unique_columns("inventory_daily_runs"),
        )
        self.assertIn(
            ("tenant_id", "daily_run_id", "export_version", "export_format"),
            unique_columns("inventory_exports"),
        )

    def test_document_and_page_relationships_include_tenant_id(self) -> None:
        for table_name in ("inventory_documents", "inventory_document_pages"):
            for constraint in Base.metadata.tables[table_name].constraints:
                if isinstance(constraint, ForeignKeyConstraint):
                    local_columns = set(constraint.columns.keys())
                    remote_tables = {
                        element.column.table.name for element in constraint.elements
                    }
                    if remote_tables != {"tenants"}:
                        self.assertIn("tenant_id", local_columns)
