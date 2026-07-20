import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class SearchOperationsMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "search-operations.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertIn("search_operation_runs", tables)
            self.assertIn("search_operation_items", tables)
            engine.dispose()

            command.downgrade(config, "0006_metadata_sidecars")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("search_operation_runs", tables)
            self.assertIn("metadata_sidecar_exports", tables)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
