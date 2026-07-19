import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class DynamicMetadataMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn("metadata_profiles", inspector.get_table_names())
            self.assertIn("asset_ai_analyses", inspector.get_table_names())
            indexes = {item["name"]: item for item in inspector.get_indexes("asset_ai_analyses")}
            self.assertTrue(indexes["uq_asset_ai_analyses_normal_run"]["unique"])
            engine.dispose()

            command.downgrade(config, "0003_managed_asset_storage")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("metadata_profiles", tables)
            self.assertNotIn("asset_ai_analyses", tables)
            self.assertIn("asset_storage_objects", tables)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
