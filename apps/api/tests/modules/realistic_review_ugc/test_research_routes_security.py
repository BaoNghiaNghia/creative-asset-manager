"""Security and reversible migration checks for Research Intelligence."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.modules.realistic_review_ugc.research_router import router


def test_research_routes_are_private_by_default():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/v1/realistic-review-ugc/research/signals").status_code == 401
    assert client.get("/api/v1/realistic-review-ugc/research/stage0-export").status_code == 401
    assert client.post("/api/v1/realistic-review-ugc/research/signals/import",
                       json={"signals":[{"source":"rss","channel":"hats",
                                         "title":"Hats","url":"https://news.google.com/a"}]}).status_code == 401
    assert client.post("/api/v1/realistic-review-ugc/research/scout-agents/agent/signals/import",
                       json={"signals":[{"source":"rss","channel":"hats",
                                         "title":"Hats","url":"https://news.google.com/a"}]}).status_code == 401


def test_additive_research_migration_upgrade_and_downgrade_in_isolation():
    path = (Path(__file__).resolve().parents[5]
            / "database/migrations/versions/0147_rrugc_research_intelligence.py")
    assert path.is_file()
    spec = importlib.util.spec_from_file_location("cam_research_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        module.op = operations
        module.upgrade()
        names = inspect(connection).get_table_names()
        assert "rrugc_research_signals" in names
        cols = {col["name"] for col in inspect(connection).get_columns("rrugc_research_signals")}
        assert {"tenant_id", "fingerprint", "url", "status", "keyword"} <= cols
        module.downgrade()
        assert "rrugc_research_signals" not in inspect(connection).get_table_names()
    engine.dispose()
