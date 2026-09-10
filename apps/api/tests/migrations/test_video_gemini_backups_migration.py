import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class VideoGeminiBackupsMigrationTest(unittest.TestCase):
    def test_upgrade_and_clean_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            url = f"sqlite:///{path}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "0065_pipeline_job_index")
            command.upgrade(config, "0066_video_gemini_backups")
            engine = create_engine(url)
            checks = inspect(engine).get_check_constraints(
                "creative_ai_credentials"
            )
            provider_check = next(
                item
                for item in checks
                if item["name"] == "ck_creative_ai_credentials_provider"
            )
            self.assertIn("gemini_video_backup_1", provider_check["sqltext"])
            self.assertIn("gemini_video_backup_10", provider_check["sqltext"])
            engine.dispose()
            command.downgrade(config, "0065_pipeline_job_index")
            engine = create_engine(url)
            checks = inspect(engine).get_check_constraints(
                "creative_ai_credentials"
            )
            provider_check = next(
                item
                for item in checks
                if item["name"] == "ck_creative_ai_credentials_provider"
            )
            self.assertNotIn("gemini_video_backup_1", provider_check["sqltext"])
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
