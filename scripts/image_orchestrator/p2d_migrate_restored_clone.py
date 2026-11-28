#!/usr/bin/env python3
"""Operator-only P2D migration rehearsal on an independently restored clone.

Never downloads a backup, touches production, grants a claim, or certifies
G1. The operator must verify the managed remote backup and source-snapshot
count provenance outside this script. Do not use a job worker or sandbox.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import stat
from pathlib import Path

HOST = "127.0.0.1"
PORT = 55432
DB = "cam_restore_rehearsal_p2d_20261126"
ROLE = "cam_rehearsal_user"
SOURCE = "0216_etsy_listing_idea_draft"
TARGET = "0219_cam_image_approvals"
ROOT = Path(__file__).resolve().parents[2]
PROTECTED = frozenset({
    "tenants", "processing_jobs", "etsy_spy_picked",
    "etsy_spy_listing_images", "etsy_spy_listing_image_deletions",
    "cam_muse_jobs", "cam_muse_commands", "inventory_items",
    "inventory_locations", "inventory_transactions",
})
NEW = frozenset({
    "cam_image_batches", "cam_image_flow_controls", "cam_image_packs",
    "cam_image_roles", "cam_image_commands", "cam_image_capability_states",
    "cam_image_upload_intents", "cam_render_resources",
    "cam_image_plan_approvals", "cam_image_qa_approvals",
})


class Blocked(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def private_dir(path):
    require(path.is_absolute() and not path.is_symlink() and path.resolve() == path,
            "invalid_private_directory")
    s = path.stat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
            and stat.S_IMODE(s.st_mode) == 0o700, "private_directory_required")
    return path


def private_file(directory, name, maximum):
    path = directory / name
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
            and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= maximum,
            "private_file_required")
    return path


def validate_metadata(restore, snapshot):
    require(isinstance(restore, dict) and isinstance(snapshot, dict), "invalid_evidence")
    require(restore.get("RESTORE_REHEARSAL") == "PASS"
            and restore.get("FILE_MATCHES_EXPECTED_CHECKSUMS") is True
            and restore.get("REHEARSAL_DATABASE_VALIDATED") is True
            and restore.get("RESTORE_TARGET") == DB and restore.get("TARGET_HOST") == HOST
            and restore.get("EXPECTED_TABLE_COUNTS_MATCHED") is True
            and restore.get("REQUIRED_TABLE_COUNTS_VERIFIED") is True
            and restore.get("REQUIRED_TABLE_COUNT") == len(PROTECTED),
            "restore_evidence_incomplete")
    sha = snapshot.get("backup_sha256")
    md5 = snapshot.get("backup_md5")
    require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha)
            and isinstance(md5, str) and re.fullmatch(r"[a-f0-9]{32}", md5)
            and restore.get("BACKUP_SHA256") == sha and restore.get("BACKUP_MD5") == md5
            and snapshot.get("source_revision") == SOURCE
            and type(restore.get("RESTORED_TABLES")) is int and restore["RESTORED_TABLES"] >= len(PROTECTED),
            "source_identity_mismatch")
    counts = snapshot.get("row_counts")
    require(isinstance(counts, dict) and set(counts) == PROTECTED
            and all(type(v) is int and 0 <= v <= 9223372036854775807 for v in counts.values()),
            "source_snapshot_counts_required")
    return counts


def load_inputs(directory):
    directory = private_dir(directory)
    restore_path = private_file(directory, "restore-proof.json", 65536)
    snapshot_path = private_file(directory, "source-snapshot.json", 65536)
    pgpass = private_file(directory, "rehearsal.pgpass", 4096)
    restore = json.loads(restore_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    counts = validate_metadata(restore, snapshot)
    lines = pgpass.read_text(encoding="utf-8").splitlines()
    require(len(lines) == 1 and lines[0].startswith(f"{HOST}:{PORT}:{DB}:{ROLE}:")
            and len(lines[0]) > len(f"{HOST}:{PORT}:{DB}:{ROLE}:"),
            "isolated_pgpass_required")
    output = directory / "p2d-migration-proof.json"
    require(not os.path.lexists(output), "proof_already_exists")
    return restore, counts, pgpass, output


def migrate(restore, counts, pgpass):
    # Clear libpq target overrides. Do not load production.env.
    for key in list(os.environ):
        if key.startswith("PG") or key in ("DATABASE_URL", "ALEMBIC_CONFIG", "PYTHONPATH"):
            os.environ.pop(key, None)
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import URL
    from alembic import command
    from alembic.config import Config
    import sys
    sys.path.insert(0, str(ROOT / "apps/api"))
    url = URL.create("postgresql+psycopg", username=ROLE, host=HOST, port=PORT, database=DB)
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    engine = create_engine(url, connect_args={"passfile": str(pgpass),
        "hostaddr": HOST, "sslmode": "disable", "connect_timeout": 3,
        "options": "-c statement_timeout=300000 -c lock_timeout=5000"})

    def scalar(conn, sql):
        return conn.scalar(text(sql))

    def signatures(conn):
        i = inspect(conn)
        return {name: (
            [(c["name"], str(c["type"]), c["nullable"]) for c in i.get_columns(name, schema="public")],
            i.get_pk_constraint(name, schema="public").get("constrained_columns"),
        ) for name in i.get_table_names(schema="public")}

    def row_counts(conn):
        return {name: scalar(conn, f'SELECT COUNT(*) FROM public."{name}"') for name in PROTECTED}

    try:
        with engine.begin() as conn:
            identity = conn.execute(text(
                "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
                "current_user, (SELECT rolsuper FROM pg_roles WHERE rolname=current_user), "
                "current_setting('server_version_num')::int"
            )).one()
            require(tuple(identity[:5]) == (DB, HOST, PORT, ROLE, False)
                    and 160000 <= identity[5] < 170000, "isolated_pg16_identity_mismatch")
            revision = conn.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
            require(revision == [SOURCE], "source_revision_mismatch")
            before = signatures(conn)
            require(len(before) == restore["RESTORED_TABLES"] and not NEW.intersection(before)
                    and PROTECTED.issubset(before), "restored_schema_mismatch")
            require(row_counts(conn) == counts, "source_snapshot_count_mismatch")
            cfg = Config(str(ROOT / "apps/api/alembic.ini"))
            cfg.set_main_option("script_location", str(ROOT / "database/migrations"))
            cfg.attributes["connection"] = conn
            cfg.attributes["configure_logger"] = False
            command.upgrade(cfg, TARGET)
            after = signatures(conn)
            require(conn.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
                    == [TARGET], "target_revision_mismatch")
            require(set(after) - set(before) == NEW
                    and all(after.get(name) == signature for name, signature in before.items())
                    and row_counts(conn) == counts
                    and all(scalar(conn, f'SELECT COUNT(*) FROM public."{name}"') == 0 for name in NEW),
                    "migration_postcheck_failed")
        return {"ISOLATED_MIGRATION": "PASS", "source_revision": SOURCE,
                "target_revision": TARGET, "backup_sha256": restore["BACKUP_SHA256"],
                "old_tables_preserved": len(before), "new_empty_tables": len(NEW),
                "protected_count_parity": len(PROTECTED),
                "production_mutations": "NONE", "g1_operator_provenance_verified_by_script": False}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-directory", type=Path, required=True)
    parser.add_argument("--ack-isolated-target", action="store_true")
    parser.add_argument("--ack-independent-source-snapshot", action="store_true")
    args = parser.parse_args()
    if not (args.ack_isolated_target and args.ack_independent_source_snapshot):
        parser.error("Both operator acknowledgements required; never run on production.")
    try:
        restore, counts, pgpass, output = load_inputs(args.private_directory)
        report = migrate(restore, counts, pgpass)
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, sort_keys=True)
            f.write("\n")
        print(json.dumps(report, sort_keys=True))
    except Blocked as e:
        print("ISOLATED_MIGRATION=BLOCKED: " + str(e))
        return 2
    except Exception:
        # Driver errors may contain credential or source row values.
        print("ISOLATED_MIGRATION=BLOCKED: inspect_private_operator_environment")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
