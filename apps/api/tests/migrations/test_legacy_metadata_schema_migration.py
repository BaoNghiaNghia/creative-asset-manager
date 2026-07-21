from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.metadata.model import AssetMetadataModel, asset_tag_assignments
from app.modules.tag.model import TagModel


class LegacyMetadataSchemaMigrationTest(unittest.TestCase):
    def test_upgrade_adopts_existing_tables_and_downgrade_preserves_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{path.as_posix()}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", database_url)
            command.upgrade(config, "0017_search_lifecycle_states")

            engine = create_engine(database_url)
            Base.metadata.create_all(
                engine,
                tables=[
                    TagModel.__table__,
                    AssetMetadataModel.__table__,
                    asset_tag_assignments,
                ],
            )
            with Session(engine) as session:
                session.add(
                    TagModel(
                        id="legacy",
                        name="Legacy",
                        color="#000000",
                        is_system=False,
                    )
                )
                session.commit()
            engine.dispose()

            command.upgrade(config, "head")
            engine = create_engine(database_url)
            try:
                tables = set(inspect(engine).get_table_names())
                self.assertTrue(
                    {"tags", "asset_metadata", "asset_tag_assignments"} <= tables
                )
                with Session(engine) as session:
                    self.assertEqual(
                        session.scalar(
                            select(TagModel.name).where(TagModel.id == "legacy")
                        ),
                        "Legacy",
                    )
            finally:
                engine.dispose()

            command.downgrade(config, "0017_search_lifecycle_states")
            engine = create_engine(database_url)
            try:
                tables = set(inspect(engine).get_table_names())
                self.assertTrue(
                    {"tags", "asset_metadata", "asset_tag_assignments"} <= tables
                )
                with Session(engine) as session:
                    self.assertEqual(
                        session.scalar(
                            select(TagModel.name).where(TagModel.id == "legacy")
                        ),
                        "Legacy",
                    )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
