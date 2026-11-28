"""Synthetic full migration-chain rehearsal, never a production backup proof.
Uses only the disposable loopback PostgreSQL allocated by the P2C wrapper.
"""
from pathlib import Path
import os
import sys
import json

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "apps/api"))
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from alembic.config import Config
from alembic import command

SOURCE = "0216_etsy_listing_idea_draft"
TARGET = "0219_cam_image_approvals"
DB = "cam_p2c_isolated"
USER = "cam_p2c_test"
EXPECTED = {
    "cam_image_batches", "cam_image_flow_controls", "cam_image_packs",
    "cam_image_roles", "cam_image_commands", "cam_image_capability_states",
    "cam_image_upload_intents", "cam_render_resources",
    "cam_image_plan_approvals", "cam_image_qa_approvals",
}

def schema(conn):
    i = inspect(conn)
    return {table: {
        "columns": [(c["name"], str(c["type"]), c["nullable"]) for c in i.get_columns(table)],
        "pk": i.get_pk_constraint(table).get("constrained_columns"),
    } for table in i.get_table_names(schema="public")}

def main():
    port = int(os.environ["CAM_P2C_TEST_PORT"])
    assert 1024 <= port <= 65535 and port != 5432
    url = URL.create("postgresql+psycopg", username=USER,
                     password=os.environ["CAM_P2C_TEST_PASSWORD"],
                     host="127.0.0.1", port=port, database=DB)
    # No fallback to production environment or local app DSN.
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    engine = create_engine(url, connect_args={"connect_timeout":3,
        "options":"-c statement_timeout=15000 -c lock_timeout=5000"})
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT current_database()")) == DB
            assert conn.scalar(text("SELECT current_user")) == USER
            assert not inspect(conn).get_table_names(schema="public")
            conn.commit()
            cfg = Config(str(root/"apps/api/alembic.ini"))
            cfg.set_main_option("script_location", str(root/"database/migrations"))
            cfg.attributes["connection"] = conn
            cfg.attributes["configure_logger"] = False
            command.upgrade(cfg, SOURCE)
            conn.commit()
            rev = conn.scalar(text("SELECT version_num FROM alembic_version"))
            assert rev == SOURCE, rev
            before = schema(conn)
            # Sentinel only in disposable DB; demonstrate no unrelated data changes.
            conn.execute(text("CREATE TABLE p2d_rehearsal_sentinel (id integer PRIMARY KEY, value text NOT NULL)"))
            conn.execute(text("INSERT INTO p2d_rehearsal_sentinel VALUES (1, 'fixture-not-production')"))
            conn.commit()
            print(json.dumps({"synthetic_source": rev, "source_table_count": len(before)}),flush=True)
            command.upgrade(cfg, TARGET)
            conn.commit()
            after = schema(conn)
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == TARGET
            added = set(after) - set(before) - {"p2d_rehearsal_sentinel"}
            assert added == EXPECTED, sorted(added)
            assert all(after.get(name) == old for name, old in before.items())
            assert conn.scalar(text("SELECT value FROM p2d_rehearsal_sentinel WHERE id=1")) == "fixture-not-production"
            assert all(conn.scalar(text(f'SELECT COUNT(*) FROM "{name}"')) == 0 for name in added)
            print(json.dumps({"result":"PASS","scope":"synthetic_full_chain_not_backup_restore",
                "source":SOURCE,"target":TARGET,"old_tables_unchanged":len(before),
                "new_tables":sorted(added),"new_tables_empty":True,
                "sentinel_preserved":True,"production_mutations":False}),flush=True)
    finally:
        engine.dispose()

if __name__ == "__main__":
    main()