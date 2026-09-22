import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class VideoPlaybackDerivativeMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0089_r2_video_playback_derivative")

            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            columns = {item["name"] for item in inspector.get_columns("video_cache_objects")}
            self.assertTrue({
                "playback_r2_key",
                "playback_kind",
                "playback_status",
                "playback_size_bytes",
                "playback_reserved_bytes",
                "playback_etag",
                "playback_job_id",
                "playback_generation",
                "playback_error_code",
                "playback_updated_at",
            }.issubset(columns))
            checks = {item["name"] for item in inspector.get_check_constraints("video_cache_objects")}
            self.assertIn("ck_video_cache_playback_status", checks)
            self.assertIn("ck_video_cache_playback_size", checks)
            indexes = {item["name"] for item in inspector.get_indexes("video_cache_objects")}
            self.assertIn("ix_video_cache_playback_status", indexes)
            engine.dispose()

            command.downgrade(config, "0088_public_review_encrypted_secret")
            engine = create_engine(f"sqlite:///{path}")
            columns = {item["name"] for item in inspect(engine).get_columns("video_cache_objects")}
            self.assertNotIn("playback_status", columns)
            self.assertNotIn("playback_r2_key", columns)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
