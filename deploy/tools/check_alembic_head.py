"""Fail-closed database revision check for no-migrate CAM backend deploys."""
from __future__ import annotations

import sys
from pathlib import Path

# Direct script invocation must resolve the target release API package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config
from sqlalchemy import create_engine, text

def main() -> int:
    if len(sys.argv) != 2:
        print("ALEMBIC_PREFLIGHT=INVALID_ARGS")
        return 2
    from app.core.config import get_settings
    from app.core.database import resolve_database_url
    config = Config(sys.argv[1])
    expected = ScriptDirectory.from_config(config).get_heads()
    if len(expected) != 1:
        print("ALEMBIC_PREFLIGHT=INVALID_HEADS")
        return 2
    url = resolve_database_url(get_settings())
    if not url.startswith("postgresql"):
        print("ALEMBIC_PREFLIGHT=NOT_POSTGRESQL")
        return 2
    engine = create_engine(url, connect_args={"connect_timeout": 8})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = MigrationContext.configure(connection).get_current_heads()
    finally:
        engine.dispose()
    if tuple(current) != tuple(expected):
        print("ALEMBIC_PREFLIGHT=SCHEMA_MISMATCH")
        print("ALEMBIC_CURRENT=" + (",".join(current) if current else "unversioned"))
        print("ALEMBIC_EXPECTED=" + expected[0])
        return 3
    print("ALEMBIC_PREFLIGHT=PASS")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("ALEMBIC_PREFLIGHT=ERROR")
        raise SystemExit(2)
