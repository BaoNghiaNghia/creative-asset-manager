import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

class ProcessingPolicyMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertTrue({"tenant_processing_policies", "tenant_provider_policies", "processing_policy_audits"} <= tables)
            columns = {column["name"] for column in inspector.get_columns("processing_jobs")}
            self.assertTrue({"provider_key", "provider_scope", "concurrency_accounted"} <= columns)
            engine.dispose()
            command.downgrade(config, "0009_durable_asset_pipeline")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn("tenant_processing_policies", inspector.get_table_names())
            columns = {column["name"] for column in inspector.get_columns("processing_jobs")}
            self.assertNotIn("concurrency_accounted", columns)
            engine.dispose()
