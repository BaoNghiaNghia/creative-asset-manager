import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

class PersistentAuthMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"migration.db"; config=Config("alembic.ini")
            config.set_main_option("sqlalchemy.url",f"sqlite:///{path}")
            command.upgrade(config,"head")
            engine=create_engine(f"sqlite:///{path}"); tables=set(inspect(engine).get_table_names())
            self.assertTrue({"oauth_connections","auth_sessions","oauth_transactions","auth_audit_events"}<=tables); engine.dispose()
            command.downgrade(config,"0012_ai_batch_processing")
            engine=create_engine(f"sqlite:///{path}"); tables=set(inspect(engine).get_table_names())
            self.assertNotIn("oauth_connections",tables); self.assertIn("ai_batch_jobs",tables); engine.dispose()

if __name__=="__main__": unittest.main()
