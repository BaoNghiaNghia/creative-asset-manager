"""Phase 3C bridge canary: 0206 code may read only an exact, inert 0207 schema.

This deliberately does NOT import or run the future 0207 migration. The
0206 bridge must be independently releasable before the 0207 binary.
"""
import os
from pathlib import Path
from shutil import copyfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.database import (
    DatabaseStartupError, _trial_artwork_snapshot_schema_complete,
    validate_alembic_head,
)


@pytest.fixture(scope="module")
def bridge_0206_template(tmp_path_factory):
    # Build the 0206 migration graph once; every negative case gets a private
    # disposable copy and cannot contaminate another test.
    path = tmp_path_factory.mktemp("trial-bridge-base") / "base.sqlite"
    url = "sqlite:///" + str(path)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    # Alembic env.py prioritizes DATABASE_URL in CI unless the config
    # explicitly pins this disposable SQLite test database.
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.upgrade(config, "0206_etsy_spy_sms_status")
    engine.dispose()
    return path


@pytest.fixture
def bridge_db(tmp_path, monkeypatch, bridge_0206_template):
    path = tmp_path / "bridge.sqlite"
    copyfile(bridge_0206_template, path)
    url = "sqlite:///" + str(path)
    engine = create_engine(url)
    monkeypatch.setattr(
        "app.core.database.expected_alembic_head",
        lambda _url: "0206_etsy_spy_sms_status",
    )
    assert validate_alembic_head(engine, database_url=url) == "0206_etsy_spy_sms_status"
    yield engine, url
    engine.dispose()


def _future_schema(engine, *, weakened=None, revision="0207_cam_agent_trial_artwork_snapshots"):
    columns = {
        "tenant_id": "VARCHAR(255) NOT NULL",
        "snapshot_id": "VARCHAR(36) NOT NULL",
        "study_id": "VARCHAR(64) NOT NULL",
        "keyword_id": "VARCHAR(36) NOT NULL",
        "source_output_version_id": "VARCHAR(36) NOT NULL",
        "image_processing_job_id": "VARCHAR(36) NOT NULL",
        "brief_processing_job_id": "VARCHAR(36) NOT NULL",
        "source_remote_file_id": "VARCHAR(255) NOT NULL",
        "snapshot_asset_id": "VARCHAR(255) NOT NULL",
        "snapshot_remote_file_id": "VARCHAR(255)",
        "artifact_sha256": "VARCHAR(64) NOT NULL",
        "size_bytes": "INTEGER NOT NULL",
        "width": "INTEGER NOT NULL",
        "height": "INTEGER NOT NULL",
        "status": "VARCHAR(16) NOT NULL",
        "created_by": "VARCHAR(255) NOT NULL",
        "created_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "verified_at": "DATETIME",
    }
    if weakened == "missing_column":
        del columns["created_by"]
    if weakened == "extra_column":
        columns["unreviewed"] = "TEXT"
    if weakened == "nullable_status":
        columns["status"] = "VARCHAR(16)"
    seal = (
        "((status = 'reserved' AND snapshot_remote_file_id IS NULL AND verified_at IS NULL) "
        "OR (status = 'verified' AND snapshot_remote_file_id IS NOT NULL AND verified_at IS NOT NULL))"
    )
    if weakened == "weak_seal":
        seal = (
            "((status IN ('reserved', 'verified')) "
            "AND (snapshot_remote_file_id IS NULL OR snapshot_remote_file_id IS NOT NULL) "
            "AND (verified_at IS NULL OR verified_at IS NOT NULL))"
        )
    size = "size_bytes > 0 AND size_bytes <= 16777216"
    if weakened == "weak_size":
        size = "size_bytes > 0 OR size_bytes <= 16777216"
    dims = "width > 0 AND height > 0"
    if weakened == "weak_dims":
        dims = "width >= 0 AND height >= 0"
    constraints = [
        "PRIMARY KEY (tenant_id, snapshot_id)",
        "UNIQUE (tenant_id, source_output_version_id)",
        "UNIQUE (tenant_id, snapshot_asset_id)",
        "FOREIGN KEY (tenant_id, study_id, keyword_id) "
        "REFERENCES cam_agent_trial_exposures (tenant_id, study_id, keyword_id) ON DELETE RESTRICT",
        f"CONSTRAINT ck_cam_trial_snapshot_size CHECK ({size})",
        f"CONSTRAINT ck_cam_trial_snapshot_dims CHECK ({dims})",
        f"CONSTRAINT ck_cam_trial_snapshot_seal CHECK ({seal})",
    ]
    if weakened == "missing_fk":
        constraints = [c for c in constraints if not c.startswith("FOREIGN KEY")]
    if weakened == "cascade_fk":
        constraints = [c.replace("ON DELETE RESTRICT", "ON DELETE CASCADE") for c in constraints]
    if weakened == "missing_unique":
        constraints = [c for c in constraints if "snapshot_asset_id)" not in c]
    if weakened == "missing_seal":
        constraints = [c for c in constraints if "ck_cam_trial_snapshot_seal" not in c]
    ddl = ",\n    ".join([f"{name} {definition}" for name, definition in columns.items()] + constraints)
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE cam_agent_trial_artwork_snapshots (\n    " + ddl + "\n)")
        db.execute(text("UPDATE alembic_version SET version_num = :rev"), {"rev": revision})


def test_exact_0207_successor_is_accepted_without_writing_rows(bridge_db):
    engine, url = bridge_db
    _future_schema(engine)
    assert validate_alembic_head(engine, database_url=url) == "0206_etsy_spy_sms_status"
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM cam_agent_trial_artwork_snapshots")) == 0
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0207_cam_agent_trial_artwork_snapshots"
        )


@pytest.mark.parametrize("weakened", [
    "missing_column", "extra_column", "nullable_status", "missing_fk",
    "cascade_fk", "missing_unique", "missing_seal", "weak_seal", "weak_size", "weak_dims",
])
def test_bridge_rejects_weakened_future_schema(bridge_db, weakened):
    engine, url = bridge_db
    _future_schema(engine, weakened=weakened)
    with pytest.raises(DatabaseStartupError, match="current=0207"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_rejects_future_revision_without_snapshot_table(bridge_db):
    engine, url = bridge_db
    with engine.begin() as db:
        db.execute(text(
            "UPDATE alembic_version SET version_num='0207_cam_agent_trial_artwork_snapshots'"
        ))
    with pytest.raises(DatabaseStartupError, match="current=0207"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_rejects_unknown_revision_even_with_exact_table(bridge_db):
    engine, url = bridge_db
    _future_schema(engine, revision="0208_unreviewed")
    with pytest.raises(DatabaseStartupError, match="current=0208"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_accepts_postgresql_text_cast_rewrite(bridge_db, monkeypatch):
    """PG may render varchar comparisons as (status)::text = 'x'::text."""
    from sqlalchemy import inspect as sqlalchemy_inspect
    from app.core import database as module

    engine, url = bridge_db
    _future_schema(engine)

    class InspectorProxy:
        def __init__(self, connection):
            self.real = sqlalchemy_inspect(connection)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def get_check_constraints(self, table):
            checks = self.real.get_check_constraints(table)
            for row in checks:
                if row.get("name") == "ck_cam_trial_snapshot_seal":
                    row["sqltext"] = (
                        row["sqltext"]
                        .replace("status = 'reserved'", "(status)::text = 'reserved'::text")
                        .replace("status = 'verified'", "(status)::character varying(16) = 'verified'::text")
                    )
            return checks

    monkeypatch.setattr(module, "inspect", InspectorProxy)
    assert validate_alembic_head(engine, database_url=url) == "0206_etsy_spy_sms_status"


@pytest.mark.parametrize("weakened", [False, True])
def test_postgresql_12_16_real_check_inspection_is_fail_closed(weakened):
    """CI-only PG canary: transactional DDL in an isolated, rolled-back schema.

    Runs under the PostgreSQL 12.22/16.4 matrix; never touches production or
    persists the canary table. It is valid even after the real 0207 migration.
    """
    url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not url:
        pytest.skip("disposable PostgreSQL integration database is not configured")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL-only canary")
    engine = create_engine(url)
    seal = (
        "((status = 'reserved' AND snapshot_remote_file_id IS NULL AND verified_at IS NULL) "
        "OR (status = 'verified' AND snapshot_remote_file_id IS NOT NULL AND verified_at IS NOT NULL))"
    )
    if weakened:
        seal = (
            "status IN ('reserved', 'verified') "
            "AND (snapshot_remote_file_id IS NULL OR snapshot_remote_file_id IS NOT NULL) "
            "AND (verified_at IS NULL OR verified_at IS NOT NULL)"
        )
    try:
        with engine.connect() as db:
            tx = db.begin()
            try:
                db.exec_driver_sql("CREATE SCHEMA cam_trial_bridge_canary")
                db.exec_driver_sql("SET LOCAL search_path TO cam_trial_bridge_canary, public")
                db.exec_driver_sql(f"""
                    CREATE TABLE cam_agent_trial_artwork_snapshots (
                        tenant_id VARCHAR(255) NOT NULL,
                        snapshot_id VARCHAR(36) NOT NULL,
                        study_id VARCHAR(64) NOT NULL,
                        keyword_id VARCHAR(36) NOT NULL,
                        source_output_version_id VARCHAR(36) NOT NULL,
                        image_processing_job_id VARCHAR(36) NOT NULL,
                        brief_processing_job_id VARCHAR(36) NOT NULL,
                        source_remote_file_id VARCHAR(255) NOT NULL,
                        snapshot_asset_id VARCHAR(255) NOT NULL,
                        snapshot_remote_file_id VARCHAR(255),
                        artifact_sha256 VARCHAR(64) NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        width INTEGER NOT NULL,
                        height INTEGER NOT NULL,
                        status VARCHAR(16) NOT NULL,
                        created_by VARCHAR(255) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        verified_at TIMESTAMPTZ,
                        PRIMARY KEY (tenant_id, snapshot_id),
                        UNIQUE (tenant_id, source_output_version_id),
                        UNIQUE (tenant_id, snapshot_asset_id),
                        FOREIGN KEY (tenant_id, study_id, keyword_id)
                            REFERENCES public.cam_agent_trial_exposures
                            (tenant_id, study_id, keyword_id) ON DELETE RESTRICT,
                        CONSTRAINT ck_cam_trial_snapshot_size
                            CHECK (size_bytes > 0 AND size_bytes <= 16777216),
                        CONSTRAINT ck_cam_trial_snapshot_dims
                            CHECK (width > 0 AND height > 0),
                        CONSTRAINT ck_cam_trial_snapshot_seal CHECK ({seal})
                    )
                """)
                assert _trial_artwork_snapshot_schema_complete(db) is (not weakened)
            finally:
                tx.rollback()
    finally:
        engine.dispose()