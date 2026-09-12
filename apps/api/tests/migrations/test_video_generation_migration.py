import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class VideoGenerationMigrationTest(unittest.TestCase):
    def test_0066_to_0067_round_trip_preserves_durable_storage_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'migration.db'}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "0066_video_gemini_backups")
            command.upgrade(config, "0067_video_generation_domain")
            engine = create_engine(url)
            inspector = inspect(engine)
            self.assertIn("video_generation_runs", inspector.get_table_names())
            self.assertIn("video_generation_references", inspector.get_table_names())
            self.assertIn("storage_class", {item["name"] for item in inspector.get_columns("asset_storage_objects")})
            engine.dispose()
            command.downgrade(config, "0066_video_gemini_backups")
            engine = create_engine(url)
            inspector = inspect(engine)
            self.assertNotIn("video_generation_runs", inspector.get_table_names())
            self.assertNotIn("storage_class", {item["name"] for item in inspector.get_columns("asset_storage_objects")})
            engine.dispose()
            command.upgrade(config, "0067_video_generation_domain")
            engine = create_engine(url)
            self.assertIn("video_generation_runs", inspect(engine).get_table_names())
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
