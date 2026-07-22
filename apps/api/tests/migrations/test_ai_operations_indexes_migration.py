import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class AiOperationsIndexesMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0022_ai_operations_indexes")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn(
                "ix_asset_ai_analyses_tenant_status_created",
                {item["name"] for item in inspector.get_indexes("asset_ai_analyses")},
            )
            self.assertIn(
                "ix_ai_batch_jobs_tenant_provider_status_created",
                {item["name"] for item in inspector.get_indexes("ai_batch_jobs")},
            )
            self.assertIn(
                "ix_processing_jobs_tenant_status_created",
                {item["name"] for item in inspector.get_indexes("processing_jobs")},
            )
            engine.dispose()

            command.downgrade(config, "0021_ai_multi_governance")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn(
                "ix_processing_jobs_tenant_status_created",
                {item["name"] for item in inspector.get_indexes("processing_jobs")},
            )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
