"""Additive access-declaration table migration, without updating any shop rights."""
from pathlib import Path
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel


def test_access_declarations_additive_and_protected(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        url = "sqlite:///" + str(Path(directory) / "access-declaration-test.db")
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[BrandShopModel.__table__])
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO bc_shops (id,tenant_id,platform,region,canonical_shop_key,"
                "storefront_url,display_name,access_mode,status,created_by,created_at,updated_at)"
                " VALUES ('test-shop','tenant-a','etsy','US','hats',"
                "'https://www.etsy.com/shop/Hats','Hats','public_observation','draft',"
                "'user-a','2026-10-19 09:00:00','2026-10-19 09:00:00')"
            ))
        engine.dispose()
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url)
        command.stamp(config, "0164_branding_dna_shop_rag")
        command.upgrade(config, "0165_branding_dna_access_declarations")
        engine = create_engine(url)
        inspector = inspect(engine)
        assert "bc_shop_access_declarations" in inspector.get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT status||':'||access_mode FROM bc_shops WHERE id='test-shop'"
            )).scalar_one() == "draft:public_observation"
            assert conn.execute(text("SELECT COUNT(*) FROM bc_shop_access_declarations")).scalar_one() == 0
        checks = {c["name"] for c in inspector.get_check_constraints("bc_shop_access_declarations")}
        assert "ck_bc_shop_access_status" in checks
        assert "ck_bc_shop_access_relationship" in checks
        engine.dispose()
        with pytest.raises(RuntimeError, match="protected"):
            command.downgrade(config, "0164_branding_dna_shop_rag")
        monkeypatch.setenv("CAM_ALLOW_ACCESS_DECLARATION_DROP_FOR_TEST", "1")
        command.downgrade(config, "0164_branding_dna_shop_rag")
        engine = create_engine(url)
        assert "bc_shop_access_declarations" not in inspect(engine).get_table_names()
        engine.dispose()
