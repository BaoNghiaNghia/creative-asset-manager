import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class MetadataSidecarMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata-sidecars.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "head")

            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn("metadata_sidecar_exports", inspector.get_table_names())
            uniques = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints("metadata_sidecar_exports")
            }
            self.assertIn(("analysis_id", "storage_provider"), uniques)
            engine.dispose()

            command.downgrade(config, "0005_external_ingestions")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn("metadata_sidecar_exports", tables)
            self.assertIn("asset_ingestions", tables)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
