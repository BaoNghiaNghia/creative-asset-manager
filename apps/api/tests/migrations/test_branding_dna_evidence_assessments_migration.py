"""Audit migration is additive and cannot drop history outside disposable DBs."""
from pathlib import Path
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel


def test_assessment_history_migration_is_additive_and_protected(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        url = "sqlite:///" + str(Path(directory) / "evidence-assessment-test.db")
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[
            BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        ])
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO bc_shops (id,tenant_id,platform,region,canonical_shop_key,"
                "storefront_url,display_name,access_mode,status,created_by,created_at,updated_at)"
                " VALUES ('shop-a','tenant-a','etsy','US','sample',"
                "'https://www.etsy.com/shop/Sample','Sample','public_observation','draft',"
                "'owner','2026-10-19 09:00:00','2026-10-19 09:00:00')"
            ))
        engine.dispose()
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url",url)
        command.stamp(config, "0166_branding_dna_access_reviews")
        command.upgrade(config, "0167_branding_dna_evidence_assessments")
        engine = create_engine(url)
        inspector=inspect(engine)
        assert "bc_listing_evidence_assessments" in inspector.get_table_names()
        checks={x["name"] for x in inspector.get_check_constraints(
            "bc_listing_evidence_assessments")}
        assert "ck_bc_evidence_assessment_decision" in checks
        assert "ck_bc_evidence_assessment_rights_basis" in checks
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT status||':'||access_mode FROM bc_shops WHERE id='shop-a'"
            )).scalar_one() == "draft:public_observation"
            assert conn.execute(text(
                "SELECT COUNT(*) FROM bc_listing_snapshots"
            )).scalar_one() == 0
        engine.dispose()
        with pytest.raises(RuntimeError, match="protected"):
            command.downgrade(config,"0166_branding_dna_access_reviews")
        monkeypatch.setenv("CAM_ALLOW_EVIDENCE_ASSESSMENT_DROP_FOR_TEST","1")
        command.downgrade(config,"0166_branding_dna_access_reviews")
        engine=create_engine(url)
        assert "bc_listing_evidence_assessments" not in inspect(engine).get_table_names()
        assert "bc_listing_snapshots" in inspect(engine).get_table_names()
        engine.dispose()
