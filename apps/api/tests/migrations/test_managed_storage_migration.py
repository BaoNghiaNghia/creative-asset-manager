import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ManagedStorageMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn("asset_storage_objects", inspector.get_table_names())
            uniques = {
                item["name"]
                for item in inspector.get_unique_constraints("asset_storage_objects")
            }
            self.assertIn("uq_asset_storage_objects_asset_provider", uniques)
            engine.dispose()

            command.downgrade(config, "0002_processing_jobs_outbox")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("asset_storage_objects", tables)
            self.assertIn("processing_jobs", tables)
            engine.dispose()
