import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ProcessingJobDurationMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0029_processing_job_duration")
            engine = create_engine(f"sqlite:///{path}")
            columns = {item["name"] for item in inspect(engine).get_columns("processing_jobs")}
            self.assertIn("processing_duration_ms", columns)
            engine.dispose()

            command.downgrade(config, "0028_central_authorization")
            engine = create_engine(f"sqlite:///{path}")
            columns = {item["name"] for item in inspect(engine).get_columns("processing_jobs")}
            self.assertNotIn("processing_duration_ms", columns)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()