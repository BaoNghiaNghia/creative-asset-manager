"""Exercise only the additive BD-01 migration against isolated SQLite schema.

No production database, credentials or existing user data is used.
"""
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel


def test_additive_evidence_migration_and_head():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "evidence.db"
        url = f"sqlite:///{path}"
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[BrandShopModel.__table__, BrandWorkerModel.__table__])
        engine.dispose()

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.stamp(config, "0158_branding_dna_agent_auth")
        command.upgrade(config, "0159_branding_dna_evidence")

        engine = create_engine(url)
        inspection = inspect(engine)
        assert {"bc_shops", "bc_browser_agents", "bc_listing_snapshots",
                "bc_listing_images"}.issubset(inspection.get_table_names())
        assert "uq_bc_listing_image_gallery_order" in {
            item["name"] for item in inspection.get_unique_constraints("bc_listing_images")
        }
        assert any(set(entry["constrained_columns"]) == {"tenant_id", "shop_id", "snapshot_id"}
                   for entry in inspection.get_foreign_keys("bc_listing_images"))
        with engine.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == "0159_branding_dna_evidence"
        engine.dispose()
