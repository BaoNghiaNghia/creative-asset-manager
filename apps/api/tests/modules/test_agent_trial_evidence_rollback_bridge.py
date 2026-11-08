"""Phase 3C.3B0: only a strictly validated 0205 ledger is rollback-compatible.

This test synthesizes future 0205 tables WITHOUT adding the migration or
activating any experiment, exposure, provider call or QA capture.
"""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.database import (
    DatabaseStartupError, _trial_evidence_schema_complete,
    validate_alembic_head,
)


@pytest.fixture
def old_release_db(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "evidence-bridge.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.upgrade(config, "0204_cam_agent_holdout_studies")
    monkeypatch.setattr(
        "app.core.database.expected_alembic_head",
        lambda _url: "0204_cam_agent_holdout_studies",
    )
    assert validate_alembic_head(engine, database_url=url) == "0204_cam_agent_holdout_studies"
    yield engine, url
    engine.dispose()


def _simulate_future_0205(
    engine, *,
    assignment_fk=True, job_fk=True, exposure_unique=True,
    qa_fk=True, output_fk=True, qa_unique=True,
    arm_check=True, verdict_check=True, critical_check=True,
):
    with engine.begin() as c:
        c.exec_driver_sql(f"""
            CREATE TABLE cam_agent_trial_exposures (
                tenant_id VARCHAR(255) NOT NULL,
                study_id VARCHAR(64) NOT NULL,
                keyword_id VARCHAR(36) NOT NULL,
                processing_job_id VARCHAR(36) NOT NULL,
                arm VARCHAR(12) NOT NULL,
                policy_version VARCHAR(32) NOT NULL,
                policy_sha256 VARCHAR(64) NOT NULL,
                worker_identity VARCHAR(96) NOT NULL,
                applied_at DATETIME NOT NULL,
                PRIMARY KEY (tenant_id, study_id, keyword_id)
                {", UNIQUE (tenant_id, processing_job_id)" if exposure_unique else ""}
                {", FOREIGN KEY (tenant_id, study_id, keyword_id) REFERENCES cam_agent_trial_assignments (tenant_id, study_id, keyword_id) ON DELETE RESTRICT" if assignment_fk else ""}
                {", FOREIGN KEY (processing_job_id) REFERENCES processing_jobs (id) ON DELETE RESTRICT" if job_fk else ""}
                {", CONSTRAINT ck_cam_trial_exposure_arm CHECK (1 = 1)" if arm_check == "weak" else ", CONSTRAINT ck_cam_trial_exposure_arm CHECK (arm IN ('control', 'challenger'))" if arm_check else ""}
            )
        """)
        c.exec_driver_sql(f"""
            CREATE TABLE cam_agent_trial_qa_reviews (
                tenant_id VARCHAR(255) NOT NULL,
                review_id VARCHAR(36) NOT NULL,
                study_id VARCHAR(64) NOT NULL,
                keyword_id VARCHAR(36) NOT NULL,
                output_version_id VARCHAR(36) NOT NULL,
                artifact_sha256 VARCHAR(64) NOT NULL,
                blind_token_sha256 VARCHAR(64) NOT NULL,
                reviewer_id VARCHAR(255) NOT NULL,
                verdict VARCHAR(12) NOT NULL,
                critical_defect BOOLEAN NOT NULL,
                reviewed_at DATETIME NOT NULL,
                PRIMARY KEY (tenant_id, review_id)
                {", UNIQUE (tenant_id, output_version_id)" if qa_unique else ""}
                {", FOREIGN KEY (tenant_id, study_id, keyword_id) REFERENCES cam_agent_trial_exposures (tenant_id, study_id, keyword_id) ON DELETE RESTRICT" if qa_fk else ""}
                {", FOREIGN KEY (output_version_id) REFERENCES rrugc_image_output_versions (id) ON DELETE RESTRICT" if output_fk else ""}
                {", CONSTRAINT ck_cam_trial_qa_verdict CHECK (verdict IN ('accepted', 'rejected'))" if verdict_check else ""}
                {", CONSTRAINT ck_cam_trial_qa_critical CHECK (NOT (critical_defect AND verdict = 'accepted'))" if critical_check else ""}
            )
        """)
        c.execute(text(
            "UPDATE alembic_version SET version_num='0205_cam_agent_trial_evidence'"
        ))


def test_bridge_accepts_only_exact_0205_evidence_schema(old_release_db):
    engine, url = old_release_db
    _simulate_future_0205(engine)
    with engine.connect() as connection:
        assert _trial_evidence_schema_complete(connection)
    assert validate_alembic_head(engine, database_url=url) == "0204_cam_agent_holdout_studies"


@pytest.mark.parametrize("weakened", [
    "assignment_fk", "job_fk", "exposure_unique", "qa_fk", "output_fk",
    "qa_unique", "arm_check", "verdict_check", "critical_check",
])
def test_bridge_rejects_weakened_0205_evidence(old_release_db, weakened):
    engine, url = old_release_db
    _simulate_future_0205(engine, **{weakened: False})
    with pytest.raises(DatabaseStartupError, match="current=0205"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_rejects_named_but_semantically_weak_check(old_release_db):
    engine, url = old_release_db
    _simulate_future_0205(engine, arm_check="weak")
    with pytest.raises(DatabaseStartupError, match="current=0205"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_rejects_0205_version_without_tables(old_release_db):
    engine, url = old_release_db
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE alembic_version SET version_num='0205_cam_agent_trial_evidence'"
        ))
    with pytest.raises(DatabaseStartupError, match="current=0205"):
        validate_alembic_head(engine, database_url=url)


def test_bridge_rejects_unreviewed_future_even_with_complete_tables(old_release_db):
    engine, url = old_release_db
    _simulate_future_0205(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE alembic_version SET version_num='0206_unreviewed'"
        ))
    with pytest.raises(DatabaseStartupError, match="0206_unreviewed"):
        validate_alembic_head(engine, database_url=url)


def test_old_0203_release_must_not_skip_two_migrations(old_release_db, monkeypatch):
    engine, url = old_release_db
    _simulate_future_0205(engine)
    monkeypatch.setattr(
        "app.core.database.expected_alembic_head",
        lambda _url: "0203_cam_agent_harness_store",
    )
    with pytest.raises(DatabaseStartupError, match="current=0205"):
        validate_alembic_head(engine, database_url=url)
