"""Additive documentary review migration, isolated rollback, no shop rights changes."""
from pathlib import Path
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel


def test_access_reviews_migration_is_additive_and_protected(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        url = "sqlite:///" + str(Path(directory) / "access-review-test.db")
        engine = create_engine(url)
        Base.metadata.create_all(
            engine, tables=[BrandShopModel.__table__, BrandShopAccessDeclarationModel.__table__]
        )
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO bc_shops (id,tenant_id,platform,region,canonical_shop_key,"
                "storefront_url,display_name,access_mode,status,created_by,created_at,updated_at)"
                " VALUES ('shop-a','tenant-a','etsy','US','fixture',"
                "'https://www.etsy.com/shop/Fixture','Fixture','public_observation','draft',"
                "'curator','2026-10-19 09:00:00','2026-10-19 09:00:00')"
            ))
        engine.dispose()
        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "0165_branding_dna_access_declarations")
        command.upgrade(cfg, "0166_branding_dna_access_reviews")
        engine = create_engine(url)
        check = inspect(engine)
        assert "bc_shop_access_reviews" in check.get_table_names()
        names = {row["name"] for row in check.get_check_constraints("bc_shop_access_reviews")}
        assert "ck_bc_access_review_decision" in names
        with engine.connect() as c:
            assert c.execute(text("SELECT status || ':' || access_mode FROM bc_shops")).scalar_one() == "draft:public_observation"
            assert c.execute(text("SELECT count(*) FROM bc_shop_access_declarations")).scalar_one() == 0
        engine.dispose()
        with pytest.raises(RuntimeError, match="protected"):
            command.downgrade(cfg, "0165_branding_dna_access_declarations")
        monkeypatch.setenv("CAM_ALLOW_ACCESS_REVIEW_DROP_FOR_TEST", "1")
        command.downgrade(cfg, "0165_branding_dna_access_declarations")
        engine = create_engine(url)
        assert "bc_shop_access_reviews" not in inspect(engine).get_table_names()
        assert "bc_shop_access_declarations" in inspect(engine).get_table_names()
        engine.dispose()
