"""Phase 3C.2 rollback bridge: structurally gated 0203 -> 0204 startup."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.database import DatabaseStartupError, validate_alembic_head


@pytest.fixture(autouse=True)
def pretend_previous_0203_release(monkeypatch):
    monkeypatch.setattr(
        "app.core.database.expected_alembic_head",
        lambda url: "0203_cam_agent_harness_store",
    )


def _prepare(tmp_path):
    url = "sqlite:///" + str(tmp_path / "bridge.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.upgrade(config, "0203_cam_agent_harness_store")
    assert validate_alembic_head(engine, database_url=url) == "0203_cam_agent_harness_store"
    return engine, url


def _future_schema(engine, *, unique=True, assignment_fk=True):
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE cam_agent_trials (
                tenant_id VARCHAR(255) NOT NULL,
                study_id VARCHAR(64) NOT NULL,
                cohort VARCHAR(20) NOT NULL,
                candidate_event_key VARCHAR(64) NOT NULL,
                agent_version VARCHAR(20) NOT NULL,
                trial_version VARCHAR(32) NOT NULL,
                holdout_percent INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL,
                registered_by VARCHAR(255) NOT NULL,
                registered_at DATETIME NOT NULL,
                PRIMARY KEY (tenant_id, study_id),
                FOREIGN KEY (tenant_id, candidate_event_key)
                    REFERENCES cam_agent_experiences (tenant_id, event_key)
            )
        """)
        unique_clause = ", UNIQUE (tenant_id, keyword_id)" if unique else ""
        fk_clause = (
            ", FOREIGN KEY (tenant_id, study_id) "
            "REFERENCES cam_agent_trials (tenant_id, study_id)"
            if assignment_fk else ""
        )
        connection.exec_driver_sql("""
            CREATE TABLE cam_agent_trial_assignments (
                tenant_id VARCHAR(255) NOT NULL,
                study_id VARCHAR(64) NOT NULL,
                keyword_id VARCHAR(36) NOT NULL,
                keyword_sha256 VARCHAR(64) NOT NULL,
                arm VARCHAR(12) NOT NULL,
                assigned_at DATETIME NOT NULL,
                PRIMARY KEY (tenant_id, study_id, keyword_id)
            """ + unique_clause + fk_clause + ")")
        connection.execute(text(
            "UPDATE alembic_version SET version_num='0204_cam_agent_holdout_studies'"
        ))


def test_rollback_bridge_accepts_only_structurally_complete_0204(tmp_path):
    engine, url = _prepare(tmp_path)
    try:
        _future_schema(engine)
        # This checkout intentionally has HEAD 0203; it must still start only
        # when the successor has the complete, non-weakened additive schema.
        assert validate_alembic_head(engine, database_url=url) == "0203_cam_agent_harness_store"
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE cam_agent_trial_assignments")
        with pytest.raises(DatabaseStartupError, match="current=0204"):
            validate_alembic_head(engine, database_url=url)
    finally:
        engine.dispose()


@pytest.mark.parametrize("unique,assignment_fk", [(False, True), (True, False)])
def test_rollback_bridge_refuses_weakened_exposure_constraints(tmp_path, unique, assignment_fk):
    engine, url = _prepare(tmp_path)
    try:
        _future_schema(engine, unique=unique, assignment_fk=assignment_fk)
        with pytest.raises(DatabaseStartupError, match="current=0204"):
            validate_alembic_head(engine, database_url=url)
    finally:
        engine.dispose()


def test_rollback_bridge_refuses_future_version_without_tables(tmp_path):
    engine, url = _prepare(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE alembic_version SET version_num='0204_cam_agent_holdout_studies'"
            ))
        with pytest.raises(DatabaseStartupError, match="current=0204"):
            validate_alembic_head(engine, database_url=url)
    finally:
        engine.dispose()


def test_bridge_rejects_unreviewed_head_even_if_tables_present(tmp_path):
    engine, url = _prepare(tmp_path)
    try:
        _future_schema(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE alembic_version SET version_num='0205_unreviewed'"
            ))
        with pytest.raises(DatabaseStartupError, match="0205_unreviewed"):
            validate_alembic_head(engine, database_url=url)
    finally:
        engine.dispose()
