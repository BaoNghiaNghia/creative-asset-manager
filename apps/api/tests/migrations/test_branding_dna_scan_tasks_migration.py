"""BD-01 queue migration smoke on an isolated SQLite database."""
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel


def test_task_queue_migration_is_additive_and_scoped():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "queue-migration.db"
        url = f"sqlite:///{path}"
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[
            BrandShopModel.__table__, BrandWorkerModel.__table__,
            BrandListingSnapshotModel.__table__, BrandListingImageModel.__table__,
        ])
        engine.dispose()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.stamp(config, "0159_branding_dna_evidence")
        command.upgrade(config, "0160_branding_dna_scan_tasks")
        engine = create_engine(url)
        inspector = inspect(engine)
        assert {"bc_shops", "bc_listing_images", "bc_listing_snapshots", "bc_scan_tasks"} <= set(inspector.get_table_names())
        assert "uq_bc_scan_task_idempotency" in {
            item["name"] for item in inspector.get_unique_constraints("bc_scan_tasks")
        }
        assert any(set(fk["constrained_columns"]) == {"tenant_id", "shop_id", "snapshot_id"}
                   for fk in inspector.get_foreign_keys("bc_scan_tasks"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0160_branding_dna_scan_tasks"
        engine.dispose()
