import os

import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.database import expected_alembic_head


@pytest.mark.integration
def test_public_review_schema_is_present_at_postgresql_head():
    url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("requires PostgreSQL integration database")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"public_shares", "public_share_sessions", "public_share_guests", "asset_annotations"} <= tables
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == expected_alembic_head(url)
    finally:
        engine.dispose()
