import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class DynamicGeminiKeyPoolsMigrationTest(unittest.TestCase):
    def test_upgrade_allows_dynamic_creative_and_inventory_backup_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            url = f"sqlite:///{path}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            # 0066 already contains both credential tables and the legacy
            # fixed 1..10 Creative constraint. Later unrelated migrations include
            # PostgreSQL-only operations, so stamp their state and exercise 0138
            # itself in isolation on SQLite.
            command.upgrade(config, "0066_video_gemini_backups")
            command.stamp(config, "0137_rrugc_keyword_favorites")
            command.upgrade(config, "0138_dynamic_gemini_key_pools")

            engine = create_engine(url)
            inspector = inspect(engine)
            creative = next(
                item for item in inspector.get_check_constraints("creative_ai_credentials")
                if item["name"] == "ck_creative_ai_credentials_provider"
            )["sqltext"]
            inventory = next(
                item for item in inspector.get_check_constraints("inventory_ai_credentials")
                if item["name"] == "ck_inventory_ai_credentials_provider"
            )["sqltext"]
            self.assertIn("gemini!_backup!_%", creative)
            self.assertIn("gemini!_video!_backup!_%", creative)
            self.assertIn("gemini!_backup!_%", inventory)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
