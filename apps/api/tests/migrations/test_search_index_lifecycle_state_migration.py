import tempfile
import unittest
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

class SearchIndexLifecycleStateMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_constraint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            checks = {row["name"]: row["sqltext"] for row in inspect(engine).get_check_constraints("search_index_records")}
            self.assertIn("verified", checks["ck_search_index_lifecycle_state"])
            self.assertIn("activating", checks["ck_search_index_lifecycle_state"])
            engine.dispose()
            command.downgrade(config, "0016_active_analysis_integrity")
            engine = create_engine(f"sqlite:///{path}")
            checks = {row["name"]: row["sqltext"] for row in inspect(engine).get_check_constraints("search_index_records")}
            self.assertNotIn("verified", checks["ck_search_index_lifecycle_state"])
            self.assertNotIn("activating", checks["ck_search_index_lifecycle_state"])
            engine.dispose()

if __name__ == "__main__":
    unittest.main()
