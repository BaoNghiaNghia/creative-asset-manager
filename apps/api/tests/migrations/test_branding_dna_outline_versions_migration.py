"""DNA outline versioned audit migration is additive and rollback guarded."""
from pathlib import Path
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel


def test_immutable_outline_migration_is_additive_and_rollback_is_guarded(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        url = "sqlite:///" + str(Path(directory) / "dna-outline-test.db")
        engine = create_engine(url)
        Base.metadata.create_all(engine,tables=[BrandShopModel.__table__])
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO bc_shops (id,tenant_id,platform,region,canonical_shop_key,"
                "storefront_url,display_name,access_mode,status,created_by,created_at,updated_at)"
                " VALUES ('shop-a','tenant-a','etsy','US','sample',"
                "'https://www.etsy.com/shop/Sample','Sample','public_observation','draft',"
                "'operator','2026-10-19 09:00:00','2026-10-19 09:00:00')"
            ))
        engine.dispose()
        config=Config(str(Path(__file__).resolve().parents[2]/"alembic.ini"))
        config.set_main_option("sqlalchemy.url",url)
        command.stamp(config,"0167_branding_dna_evidence_assessments")
        command.upgrade(config,"0168_branding_dna_outline_versions")
        engine=create_engine(url)
        inspector=inspect(engine)
        assert "bc_dna_outline_versions" in inspector.get_table_names()
        assert "bc_dna_outline_reviews" in inspector.get_table_names()
        unique={tuple(x["column_names"]) for x in inspector.get_unique_constraints("bc_dna_outline_versions")}
        assert ("tenant_id","shop_id","version_number") in unique
        assert ("tenant_id","shop_id","input_fingerprint") in unique
        with engine.connect() as c:
            assert c.execute(text("SELECT status||':'||access_mode FROM bc_shops")).scalar_one()=="draft:public_observation"
        engine.dispose()
        with pytest.raises(RuntimeError,match="protected"):
            command.downgrade(config,"0167_branding_dna_evidence_assessments")
        monkeypatch.setenv("CAM_ALLOW_DNA_OUTLINE_DROP_FOR_TEST","1")
        command.downgrade(config,"0167_branding_dna_evidence_assessments")
        engine=create_engine(url)
        assert "bc_dna_outline_versions" not in inspect(engine).get_table_names()
        assert "bc_dna_outline_reviews" not in inspect(engine).get_table_names()
        assert "bc_shops" in inspect(engine).get_table_names()
        engine.dispose()
