from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class AiProviderSelectionMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_analysis_identity_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{path.as_posix()}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", database_url)
            command.upgrade(config, "0018_legacy_metadata_schema")

            engine = create_engine(database_url)
            try:
                legacy = {
                    value["name"]: value
                    for value in inspect(engine).get_indexes(
                        "asset_ai_analyses"
                    )
                }["uq_asset_ai_analyses_normal_run"]
                self.assertNotIn("ai_provider", legacy["column_names"])
                self.assertNotIn("ai_model", legacy["column_names"])
            finally:
                engine.dispose()

            command.upgrade(config, "head")
            engine = create_engine(database_url)
            try:
                current = {
                    value["name"]: value
                    for value in inspect(engine).get_indexes(
                        "asset_ai_analyses"
                    )
                }["uq_asset_ai_analyses_normal_run"]
                self.assertEqual(
                    current["column_names"][-2:],
                    ["ai_provider", "ai_model"],
                )
            finally:
                engine.dispose()

            command.downgrade(config, "0018_legacy_metadata_schema")
            engine = create_engine(database_url)
            try:
                restored = {
                    value["name"]: value
                    for value in inspect(engine).get_indexes(
                        "asset_ai_analyses"
                    )
                }["uq_asset_ai_analyses_normal_run"]
                self.assertNotIn("ai_provider", restored["column_names"])
                self.assertNotIn("ai_model", restored["column_names"])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
