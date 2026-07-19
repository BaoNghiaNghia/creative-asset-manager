import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ExternalIngestionMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external-ingestion.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")

            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertTrue(
                {
                    "external_api_credentials",
                    "external_api_rate_limits",
                    "asset_ingestions",
                    "asset_ingestion_items",
                }.issubset(tables)
            )
            ingestion_uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints("asset_ingestions")
            }
            self.assertIn(
                ("tenant_id", "external_source_id", "idempotency_key"),
                ingestion_uniques,
            )
            engine.dispose()

            command.downgrade(config, "0004_dynamic_ai_metadata")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("asset_ingestions", tables)
            self.assertNotIn("external_api_credentials", tables)
            self.assertIn("asset_ai_analyses", tables)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
