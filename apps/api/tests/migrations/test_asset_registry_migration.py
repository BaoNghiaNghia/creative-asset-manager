import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from sqlalchemy.orm import Session

from app.modules.assets.repository import AssetRegistryRepository

class AssetRegistryMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_asset_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{database_path}")
            inspector = inspect(engine)
            expected = {
                "external_sources",
                "source_assets",
                "assets",
                "asset_source_links",
                "source_sync_cursors",
            }
            self.assertTrue(expected.issubset(set(inspector.get_table_names())))

            asset_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("assets")
            }
            source_asset_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("source_assets")
            }
            self.assertIn("uq_assets_tenant_content_hash", asset_uniques)
            self.assertIn("uq_source_assets_source_identity", source_asset_uniques)

            with Session(engine, expire_on_commit=False) as session:
                repository = AssetRegistryRepository(session)
                source = repository.upsert_external_source(
                    tenant_id="tenant-a",
                    source_key="migration-source",
                    source_type="google_drive",
                )
                source_asset = repository.upsert_source_asset(
                    tenant_id="tenant-a",
                    external_source_id=source.id,
                    external_asset_id="external-1",
                )
                asset = repository.create_asset(
                    tenant_id="tenant-a", content_hash="d" * 64
                )
                repository.link_source_asset(
                    tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id
                )
            engine.dispose()

            command.downgrade(config, "base")
            engine = create_engine(f"sqlite:///{database_path}")
            self.assertTrue(expected.isdisjoint(set(inspect(engine).get_table_names())))
            engine.dispose()
