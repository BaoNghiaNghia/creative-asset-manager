"""Isolated additive handoff queue migration safety regression.

The test stamps an isolated schema; it never reads or writes production data.
"""
import tempfile
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel


def test_staged_handoff_migration_is_additive_and_default_unverified(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        url = "sqlite:///" + str(Path(directory) / "handoff.db")
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[BrandShopModel.__table__])
        engine.dispose()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.stamp(config, "0161_branding_dna_worker_events")
        command.upgrade(config, "0162_branding_dna_handoff")
        engine = create_engine(url)
        inspector = inspect(engine)
        assert "bc_shops" in inspector.get_table_names()
        assert "bc_handoff_candidates" in inspector.get_table_names()
        assert "uq_bc_handoff_candidate_replay" in {
            c["name"] for c in inspector.get_unique_constraints("bc_handoff_candidates")
        }
        assert any(fk["referred_table"] == "bc_shops"
                   for fk in inspector.get_foreign_keys("bc_handoff_candidates"))
        with engine.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == "0162_branding_dna_handoff"
            constraints = inspector.get_check_constraints("bc_handoff_candidates")
            status_check = next(c for c in constraints if c["name"] == "ck_bc_handoff_review_status")
            assert "verified" not in status_check["sqltext"]
        engine.dispose()

        with pytest.raises(RuntimeError, match="protected"):
            command.downgrade(config, "0161_branding_dna_worker_events")
        # An explicit opt-in is honored only for the disposable test database.
        monkeypatch.setenv("CAM_ALLOW_HANDOFF_HISTORY_DROP_FOR_TEST", "1")
        command.downgrade(config, "0161_branding_dna_worker_events")
        engine = create_engine(url)
        assert "bc_handoff_candidates" not in inspect(engine).get_table_names()
        engine.dispose()
        command.upgrade(config, "0162_branding_dna_handoff")
