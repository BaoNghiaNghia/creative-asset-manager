"""Validate isolated additive 0202 schema and its explicit downgrade boundary."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_agent_harness_additive_schema_and_downgrade(tmp_path):
    url = "sqlite:///" + str(tmp_path / "cam-agent-harness.sqlite")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False

    engine = create_engine(url)
    command.stamp(config, "0201_etsy_spy_parent_keywords")
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert {"cam_agent_definitions", "cam_agent_experiences"}.issubset(inspector.get_table_names())
    defs = {column["name"] for column in inspector.get_columns("cam_agent_definitions")}
    experience = {column["name"] for column in inspector.get_columns("cam_agent_experiences")}
    assert {"tenant_id", "agent_id", "version", "enabled"}.issubset(defs)
    assert {"tenant_id", "event_key", "status", "source_event_ids_json"}.issubset(experience)

    with engine.begin() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0202_cam_agent_harness_store"
        conn.execute(text(
            "INSERT INTO cam_agent_definitions "
            "(tenant_id,agent_id,version,capabilities_json,allowed_tools_json,"
            "preferred_executor,fallback_executors_json,max_concurrent,max_retries,"
            "created_by,created_at) VALUES "
            "('boostgame','gatorhats.typography','v1','[]','[]','codex','[]',1,0,"
            "'reviewer',CURRENT_TIMESTAMP)"
        ))
        assert conn.scalar(text(
            "SELECT enabled FROM cam_agent_definitions WHERE tenant_id='boostgame'"
        )) in (False, 0)
        conn.execute(text("DELETE FROM cam_agent_definitions"))

    # Destructive schema downgrade is only tested on an empty disposable DB.
    command.downgrade(config, "0201_etsy_spy_parent_keywords")
    assert not {"cam_agent_definitions", "cam_agent_experiences"}.intersection(
        inspect(engine).get_table_names()
    )
    engine.dispose()
