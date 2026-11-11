"""3C.3B2C2A: additive snapshot registry is independent of disposable output.

Migration may not delete artifacts or quietly erase a non-empty QA registry.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.modules.agent_harness.model import AgentTrialArtworkSnapshotModel
from app.core.database import validate_alembic_head, _trial_artwork_snapshot_schema_complete


@pytest.fixture
def scratch_0206(tmp_path):
    url = "sqlite:///" + str(tmp_path / "trial-snapshot.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    # Explicitly isolate the scratch database from CI DATABASE_URL. The
    # Alembic env prefers this attribute over the ambient PostgreSQL URL.
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    engine = create_engine(url)
    command.upgrade(config, "0206_etsy_spy_sms_status")
    yield engine, config
    engine.dispose()


def _insert(db, *, status="reserved"):
    db.execute(text("""
        INSERT INTO cam_agent_trial_artwork_snapshots
        (tenant_id,snapshot_id,study_id,keyword_id,source_output_version_id,
         image_processing_job_id,brief_processing_job_id,source_remote_file_id,
         snapshot_asset_id,snapshot_remote_file_id,artifact_sha256,size_bytes,
         width,height,status,created_by,verified_at)
        VALUES
        ('boostgame','snapshot-001','study-001','keyword-001','output-001',
         'image-job-001','brief-job-001','source-001',
         'trial-snapshot-001',:remote,:sha,130,256,128,:status,'operator',:when)
    """), {
        "remote": "separate-remote-001" if status == "verified" else None,
        "when": "2026-11-10 00:00:00" if status == "verified" else None,
        "sha": "a" * 64, "status": status,
    })


def test_additive_0207_snapshot_has_no_output_history_fk_and_starts_empty(scratch_0206):
    engine, config = scratch_0206
    with engine.connect() as db:
        before = db.scalar(text("SELECT count(*) FROM etsy_spy_sms_statuses"))
    command.upgrade(config, "0207_cam_agent_trial_artwork_snapshots")
    inspector = inspect(engine)
    table = "cam_agent_trial_artwork_snapshots"
    with engine.connect() as db:
        assert _trial_artwork_snapshot_schema_complete(db)
    assert validate_alembic_head(engine, database_url=str(engine.url)) == "0207_cam_agent_trial_artwork_snapshots"
    # The deliberately reviewed 0206 rollback bridge accepts only the exact
    # additive successor and its complete sealed registry schema.
    with patch("app.core.database.expected_alembic_head", return_value="0206_etsy_spy_sms_status"):
        assert validate_alembic_head(engine, database_url=str(engine.url)) == "0206_etsy_spy_sms_status"
    assert table in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns(table)} == {
        column.name for column in AgentTrialArtworkSnapshotModel.__table__.columns
    }
    fks = inspector.get_foreign_keys(table)
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "cam_agent_trial_exposures"
    assert "rrugc_image_output_versions" not in {fk["referred_table"] for fk in fks}
    assert inspector.get_pk_constraint(table)["constrained_columns"] == [
        "tenant_id", "snapshot_id",
    ]
    assert {tuple(x["column_names"]) for x in inspector.get_unique_constraints(table)} >= {
        ("tenant_id", "source_output_version_id"),
        ("tenant_id", "snapshot_asset_id"),
    }
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM " + table)) == 0
        assert db.scalar(text("SELECT count(*) FROM etsy_spy_sms_statuses")) == before
    command.downgrade(config, "0206_etsy_spy_sms_status")
    assert table not in inspect(engine).get_table_names()


def test_registered_snapshot_refuses_destructive_downgrade(scratch_0206):
    engine, config = scratch_0206
    command.upgrade(config, "0207_cam_agent_trial_artwork_snapshots")
    with engine.begin() as db:
        _insert(db, status="verified")
    with pytest.raises(RuntimeError, match="Refusing destructive"):
        command.downgrade(config, "0206_etsy_spy_sms_status")
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM cam_agent_trial_artwork_snapshots")) == 1
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0207_cam_agent_trial_artwork_snapshots"
        )


@pytest.mark.parametrize("status,remote,verified", [
    ("verified", None, None),
    ("reserved", "remote-001", "2026-11-10"),
    ("forged", None, None),
])
def test_snapshot_seal_constraint_blocks_partial_or_forged_states(
    scratch_0206, status, remote, verified,
):
    engine, config = scratch_0206
    command.upgrade(config, "0207_cam_agent_trial_artwork_snapshots")
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(text("""
                INSERT INTO cam_agent_trial_artwork_snapshots
                (tenant_id,snapshot_id,study_id,keyword_id,source_output_version_id,
                 image_processing_job_id,brief_processing_job_id,source_remote_file_id,
                 snapshot_asset_id,snapshot_remote_file_id,artifact_sha256,size_bytes,
                 width,height,status,created_by,verified_at)
                VALUES ('boostgame','snapshot-001','study-001','keyword-001',
                        'output-001','image-001','brief-001','src-001',
                        'snapshot-asset-001',:remote,:sha,100,256,128,
                        :status,'actor',:when)
            """), {"remote":remote, "sha":"a"*64,"status":status, "when":verified})
