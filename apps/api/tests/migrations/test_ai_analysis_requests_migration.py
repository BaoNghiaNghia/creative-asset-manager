from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class AiAnalysisRequestsMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_request_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{path.as_posix()}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", database_url)
            command.upgrade(config, "0019_ai_provider_selection")

            engine = create_engine(database_url)
            try:
                self.assertNotIn(
                    "ai_analysis_requests", inspect(engine).get_table_names()
                )
            finally:
                engine.dispose()

            command.upgrade(config, "head")
            engine = create_engine(database_url)
            try:
                inspector = inspect(engine)
                self.assertIn(
                    "ai_analysis_requests", inspector.get_table_names()
                )
                self.assertIn(
                    "ai_analysis_request_items", inspector.get_table_names()
                )
                indexes = {
                    value["name"]
                    for value in inspector.get_indexes("ai_analysis_requests")
                }
                self.assertIn(
                    "ix_ai_analysis_requests_tenant_created", indexes
                )
                unique_constraints = {
                    value["name"]
                    for value in inspector.get_unique_constraints(
                        "ai_analysis_requests"
                    )
                }
                self.assertIn(
                    "uq_ai_analysis_requests_tenant_key",
                    unique_constraints,
                )
            finally:
                engine.dispose()

            command.downgrade(config, "0019_ai_provider_selection")
            engine = create_engine(database_url)
            try:
                tables = inspect(engine).get_table_names()
                self.assertNotIn("ai_analysis_requests", tables)
                self.assertNotIn("ai_analysis_request_items", tables)
                self.assertIn("asset_ai_analyses", tables)
                self.assertIn("ai_batch_jobs", tables)
                self.assertIn("processing_jobs", tables)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
