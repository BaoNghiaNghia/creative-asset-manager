"""Validate additive BD-01 worker-event migration in isolated DB only."""
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.modules.branding_dna.model import BrandWorkerModel


def test_worker_event_migration_preserves_worker_registry():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "isolated-bdna-events.sqlite"
        url = f"sqlite:///{path}"
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[BrandWorkerModel.__table__])
        engine.dispose()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.stamp(config, "0160_branding_dna_scan_tasks")
        command.upgrade(config, "0161_branding_dna_worker_events")

        engine = create_engine(url)
        inspector = inspect(engine)
        assert {"bc_browser_agents", "bc_worker_events"} <= set(inspector.get_table_names())
        assert "uq_bc_worker_event_replay" in {
            uc["name"] for uc in inspector.get_unique_constraints("bc_worker_events")
        }
        assert "ck_bc_worker_event_kind" in {
            ck["name"] for ck in inspector.get_check_constraints("bc_worker_events")
        }
        assert any("agent_id" in fk["constrained_columns"]
                   for fk in inspector.get_foreign_keys("bc_worker_events"))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0161_branding_dna_worker_events"
        engine.dispose()
