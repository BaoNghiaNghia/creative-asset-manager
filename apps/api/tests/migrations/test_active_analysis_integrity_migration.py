import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class ActiveAnalysisIntegrityMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            uniques = {
                row["name"]
                for row in inspector.get_unique_constraints("asset_ai_analyses")
            }
            self.assertIn("uq_asset_ai_analyses_active_reference", uniques)
            foreign_keys = {
                row["name"]: row
                for row in inspector.get_foreign_keys("active_asset_analyses")
            }
            exact = foreign_keys["fk_active_analysis_exact_analysis"]
            self.assertEqual(
                exact["constrained_columns"],
                ["tenant_id", "asset_id", "metadata_profile_id", "analysis_id"],
            )
            engine.dispose()

            command.downgrade(config, "0015_search_governance")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            foreign_keys = {
                row["name"]: row
                for row in inspector.get_foreign_keys("active_asset_analyses")
            }
            self.assertIn("fk_active_analysis_analysis", foreign_keys)
            self.assertNotIn("fk_active_analysis_exact_analysis", foreign_keys)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
