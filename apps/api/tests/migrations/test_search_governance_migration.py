import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class SearchGovernanceMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertTrue({
                "active_asset_analyses", "active_analysis_audits",
                "tenant_search_shadow_policies", "search_shadow_observations",
                "search_index_records", "search_index_audits",
            } <= tables)
            uniques = inspect(engine).get_unique_constraints("active_asset_analyses")
            self.assertIn("uq_active_asset_analysis_context", {row["name"] for row in uniques})
            engine.dispose()

            command.downgrade(config, "0014_reconciliation_retention")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("active_asset_analyses", tables)
            self.assertIn("source_sync_runs", tables)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
