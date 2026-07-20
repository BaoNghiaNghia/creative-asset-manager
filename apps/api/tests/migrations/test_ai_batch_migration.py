import tempfile
import unittest
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,inspect

class AiBatchMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"migration.db";config=Config("alembic.ini")
            config.set_main_option("sqlalchemy.url",f"sqlite:///{path}")
            command.upgrade(config,"head")
            engine=create_engine(f"sqlite:///{path}");tables=set(inspect(engine).get_table_names())
            self.assertTrue({"ai_batch_jobs","ai_batch_items"}<=tables);engine.dispose()
            command.downgrade(config,"0011_ai_governance_pilot")
            engine=create_engine(f"sqlite:///{path}");tables=set(inspect(engine).get_table_names())
            self.assertNotIn("ai_batch_jobs",tables);self.assertIn("ai_usage_records",tables)
            engine.dispose()
