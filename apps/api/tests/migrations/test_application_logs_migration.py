import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ApplicationLogsMigrationTest(unittest.TestCase):
    def test_upgrade_creates_log_tables_and_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "application-logs.db"
            config = Config("alembic.ini"); config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}"); inspector = inspect(engine)
            self.assertTrue({"log_applications", "application_logs"}.issubset(set(inspector.get_table_names())))
            indexes = {item["name"] for item in inspector.get_indexes("application_logs")}
            self.assertIn("ix_application_logs_expires", indexes)
            self.assertIn("ix_application_logs_app_received", indexes)
            engine.dispose()


if __name__ == "__main__": unittest.main()
