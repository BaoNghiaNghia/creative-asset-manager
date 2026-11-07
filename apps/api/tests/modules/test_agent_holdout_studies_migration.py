"""Revision 0204 additive, fully tenant-scoped and rollback-safe in isolation."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.database import Base, _holdout_study_schema_complete, validate_alembic_head
from app.modules.agent_harness.model import (
    AgentDefinitionModel, AgentExperienceModel,
    AgentHoldoutStudyModel, AgentHoldoutAssignmentModel,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel


def _db(tmp_path):
    url = "sqlite:///" + str(tmp_path / "holdout.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    # Preexisting 0203 tables must remain intact, and no other system table
    # should be migrated, deleted or rewritten by 0204.
    Base.metadata.create_all(engine, tables=[
        AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
        RrugcKeywordVolumeModel.__table__,
    ])
    command.stamp(config, "0203_cam_agent_harness_store")
    return engine, url, config


def test_0204_migration_and_empty_downgrade_preserve_existing_tables(tmp_path):
    engine, url, config = _db(tmp_path)
    try:
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0204_cam_agent_holdout_studies"
            assert _holdout_study_schema_complete(conn)
        assert validate_alembic_head(engine, database_url=url) == "0204_cam_agent_holdout_studies"
        schema = inspect(engine)
        names = set(schema.get_table_names())
        assert {
            "cam_agent_trials", "cam_agent_trial_assignments",
            "cam_agent_definitions", "cam_agent_experiences",
        }.issubset(names)
        assert any(
            item["referred_table"] == "cam_agent_experiences"
            and item["constrained_columns"] == ["tenant_id", "candidate_event_key"]
            for item in schema.get_foreign_keys("cam_agent_trials")
        )
        assert any(
            item["referred_table"] == "rrugc_keyword_volumes"
            and item["constrained_columns"] == ["keyword_id"]
            for item in schema.get_foreign_keys("cam_agent_trial_assignments")
        )
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM cam_agent_trials")).scalar() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM cam_agent_trial_assignments")).scalar() == 0

        command.downgrade(config, "0203_cam_agent_harness_store")
        assert not {"cam_agent_trials", "cam_agent_trial_assignments"}.intersection(
            inspect(engine).get_table_names()
        )
        assert {"cam_agent_definitions", "cam_agent_experiences"}.issubset(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_0204_refuses_destructive_schema_downgrade_when_studies_exist(tmp_path):
    engine, url, config = _db(tmp_path)
    try:
        command.upgrade(config, "head")
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO cam_agent_trials
                (tenant_id,study_id,cohort,candidate_event_key,
                 agent_version,trial_version,holdout_percent,candidate_source_digest,
                 registered_by)
                VALUES
                ('boostgame','test-study','music',:candidate,
                 'v1','v1',20,:digest,'operator')
            """), {"candidate": "0"*64, "digest": "1"*64})
        with pytest.raises(RuntimeError, match="Refusing destructive"):
            command.downgrade(config, "0203_cam_agent_harness_store")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0204_cam_agent_holdout_studies"
    finally:
        engine.dispose()
