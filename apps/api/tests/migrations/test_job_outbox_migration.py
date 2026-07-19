import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class JobOutboxMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{database_path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertIn("processing_jobs", tables)
            self.assertIn("outbox_events", tables)
            job_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("processing_jobs")
            }
            outbox_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("outbox_events")
            }
            self.assertIn("uq_processing_jobs_tenant_key", job_uniques)
            self.assertIn("uq_outbox_events_tenant_key", outbox_uniques)
            engine.dispose()

            command.downgrade(config, "0001_asset_registry")
            engine = create_engine(f"sqlite:///{database_path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("processing_jobs", tables)
            self.assertNotIn("outbox_events", tables)
            self.assertIn("assets", tables)
            engine.dispose()
