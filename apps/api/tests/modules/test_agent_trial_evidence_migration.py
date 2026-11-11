"""0205 evidence ledgers: additive-only, no backfill, reversible if empty."""
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import _trial_evidence_schema_complete, validate_alembic_head
from app.modules.agent_harness.model import AgentTrialExposureModel, AgentTrialQaReviewModel


@pytest.fixture
def scratch_0204(tmp_path):
    url = "sqlite:///" + str(tmp_path / "trial-evidence.sqlite")
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.upgrade(cfg, "0204_cam_agent_holdout_studies")
    yield engine, url, cfg
    engine.dispose()


def test_0205_creates_only_empty_append_only_ledgers(scratch_0204):
    engine, url, cfg = scratch_0204
    with engine.connect() as c:
        old_trial_count = c.scalar(text("SELECT count(*) FROM cam_agent_trials"))
        old_assignment_count = c.scalar(text("SELECT count(*) FROM cam_agent_trial_assignments"))
    command.upgrade(cfg, "0205_cam_agent_trial_evidence")
    # Exercise the historical 0205 validator explicitly while the repository
    # has advanced through the additive 0207 schema head.
    with patch("app.core.database.expected_alembic_head", return_value="0205_cam_agent_trial_evidence"):
        assert validate_alembic_head(engine, database_url=url) == "0205_cam_agent_trial_evidence"
    with engine.connect() as c:
        assert _trial_evidence_schema_complete(c)
        assert c.scalar(text("SELECT count(*) FROM cam_agent_trial_exposures")) == 0
        assert c.scalar(text("SELECT count(*) FROM cam_agent_trial_qa_reviews")) == 0
        assert c.scalar(text("SELECT count(*) FROM cam_agent_trials")) == old_trial_count
        assert c.scalar(text("SELECT count(*) FROM cam_agent_trial_assignments")) == old_assignment_count

    schema = inspect(engine)
    assert schema.get_pk_constraint("cam_agent_trial_exposures")["constrained_columns"] == [
        "tenant_id", "study_id", "keyword_id",
    ]
    assert schema.get_pk_constraint("cam_agent_trial_qa_reviews")["constrained_columns"] == [
        "tenant_id", "review_id",
    ]
    assert {
        column.name for column in AgentTrialExposureModel.__table__.columns
    } == {
        column["name"] for column in schema.get_columns("cam_agent_trial_exposures")
    }
    assert {
        column.name for column in AgentTrialQaReviewModel.__table__.columns
    } == {
        column["name"] for column in schema.get_columns("cam_agent_trial_qa_reviews")
    }
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM cam_agent_trial_exposures")) == 0
    command.downgrade(cfg, "0204_cam_agent_holdout_studies")
    assert "cam_agent_trial_exposures" not in inspect(engine).get_table_names()
    assert "cam_agent_trial_qa_reviews" not in inspect(engine).get_table_names()
    assert "cam_agent_trial_assignments" in inspect(engine).get_table_names()


@pytest.mark.parametrize("table", ["cam_agent_trial_exposures", "cam_agent_trial_qa_reviews"])
def test_0205_refuses_destructive_downgrade_with_evidence(scratch_0204, table):
    engine, url, cfg = scratch_0204
    command.upgrade(cfg, "0205_cam_agent_trial_evidence")
    with engine.begin() as c:
        if table == "cam_agent_trial_exposures":
            # SQLite scratch DB uses foreign_keys OFF; PostgreSQL production
            # always validates the row's referenced assignment and job.
            c.execute(text("""
                INSERT INTO cam_agent_trial_exposures
                (tenant_id,study_id,keyword_id,processing_job_id,arm,
                 policy_version,policy_sha256,worker_identity)
                VALUES ('boostgame','study-001','kw-001','job-001',
                        'control','v1',:digest,'worker')
            """), {"digest": "a" * 64})
        else:
            c.execute(text("""
                INSERT INTO cam_agent_trial_qa_reviews
                (tenant_id,review_id,study_id,keyword_id,output_version_id,
                 artifact_sha256,blind_token_sha256,reviewer_id,verdict,critical_defect)
                VALUES ('boostgame','review-001','study-001','kw-001',
                        'output-001',:artifact,:token,'independent','rejected',0)
            """), {"artifact": "b" * 64, "token": "c" * 64})
    with pytest.raises(RuntimeError, match="Refusing destructive"):
        command.downgrade(cfg, "0204_cam_agent_holdout_studies")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0205_cam_agent_trial_evidence"
        assert c.scalar(text("SELECT count(*) FROM " + table)) == 1


def test_0205_checks_cannot_mark_critical_defect_as_accepted(scratch_0204):
    engine, url, cfg = scratch_0204
    command.upgrade(cfg, "0205_cam_agent_trial_evidence")
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO cam_agent_trial_qa_reviews
                (tenant_id,review_id,study_id,keyword_id,output_version_id,
                 artifact_sha256,blind_token_sha256,reviewer_id,verdict,critical_defect)
                VALUES ('boostgame','review-001','study-001','kw-001',
                        'output-001',:artifact,:token,'independent','accepted',1)
            """), {"artifact": "a" * 64, "token": "b" * 64})
