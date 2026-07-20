import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ReconciliationRetentionMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertIn("source_sync_runs", tables)
            self.assertIn("retention_cleanup_runs", tables)
            source_columns = {column["name"] for column in inspector.get_columns("source_assets")}
            item_columns = {column["name"] for column in inspector.get_columns("asset_ingestion_items")}
            self.assertTrue({"last_seen_generation", "last_seen_at"} <= source_columns)
            self.assertTrue({
                "download_url_ciphertext", "download_url_key_version",
                "download_url_expires_at", "download_url_redacted_at",
            } <= item_columns)
            engine.dispose()

            command.downgrade(config, "0013_persistent_oauth_sessions")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn("source_sync_runs", inspector.get_table_names())
            self.assertNotIn("last_seen_generation", {
                column["name"] for column in inspector.get_columns("source_assets")
            })
            self.assertIn("oauth_connections", inspector.get_table_names())
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
