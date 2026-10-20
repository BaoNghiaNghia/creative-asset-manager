#!/usr/bin/env python3
"""Safely upgrade ONLY the restored Catinat rehearsal PostgreSQL cluster.

This script refuses production target, requires positive restore evidence,
uses a fixed loopback:55432/database/role, checks source Alembic 0168,
verifies critical preexisting row counts, then migrates to 0172.
No production configuration is loaded, and no Google Sheets are accessed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Callable, Sequence

HOST = "127.0.0.1"
PORT = 55432
DATABASE = "cam_restore_rehearsal_oct2026"
ROLE = "cam_rehearsal_user"
SOURCE_REVISION = "0168_branding_dna_outline_versions"
TARGET_REVISION = "0172_catinat_transfers"
EXPECTED_TABLES = (
    "inventory_catinat_signed_shifts",
    "inventory_catinat_approval_requests",
    "inventory_catinat_approval_events",
    "inventory_catinat_count_confirmations",
    "inventory_catinat_transfers",
    "inventory_catinat_transfer_events",
)
UNCHANGED_TABLES = ("tenants", "inventory_items", "inventory_locations", "inventory_transactions")
MANAGED_BACKUP_SHA256 = "dfdd83eb724b4d4f0e9527aa4297201f49120328f07f4be5e3b22df32a364ae2"
DEFAULT_REHEARSAL_DIR = Path("/root/catinat-db-rehearsal-20261020")


class CloneMigrationBlocked(RuntimeError):
    pass


def checked_file(path: Path, *, directory: Path) -> Path:
    if (not path.is_absolute() or path.is_symlink() or
        path.parent != directory):
        raise CloneMigrationBlocked("Evidence and credentials must be local, nonsymlink private files.")
    try:
        info = path.stat()
    except OSError as exc:
        raise CloneMigrationBlocked("Private evidence or credentials file is absent.") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600):
        raise CloneMigrationBlocked("Private file owner/mode must be current operator and 0600.")
    return path


def check_inputs(*, evidence: Path, pgpassfile: Path, outfile: Path,
                 rehearsal_dir: Path = DEFAULT_REHEARSAL_DIR) -> dict:
    if rehearsal_dir.is_symlink() or not rehearsal_dir.is_absolute():
        raise CloneMigrationBlocked("Invalid private rehearsal directory.")
    try:
        info = rehearsal_dir.stat()
    except OSError as exc:
        raise CloneMigrationBlocked("Missing private rehearsal directory.") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700):
        raise CloneMigrationBlocked("Rehearsal directory must be current-operator-owned mode 0700.")
    checked_file(evidence, directory=rehearsal_dir)
    checked_file(pgpassfile, directory=rehearsal_dir)
    if outfile.parent != rehearsal_dir or not outfile.is_absolute() or outfile.is_symlink() or outfile.exists():
        raise CloneMigrationBlocked("Migration proof must be a new file inside the private rehearsal directory.")
    try:
        proof = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CloneMigrationBlocked("Cannot decode restored-backup evidence.") from exc
    if (not isinstance(proof, dict)
        or proof.get("RESTORE_REHEARSAL") != "PASS"
        or proof.get("REHEARSAL_DATABASE_VALIDATED") is not True
        or proof.get("RESTORE_TARGET") != DATABASE
        or proof.get("TARGET_HOST") != HOST
        or proof.get("BACKUP_SHA256") != MANAGED_BACKUP_SHA256
        or not isinstance(proof.get("RESTORED_TABLES"), int)
        or proof["RESTORED_TABLES"] <= 0):
        raise CloneMigrationBlocked("Independent restore evidence does not identify the verified backup and target.")
    # Verify libpq target entry matches *only* the rehearsal target.
    try:
        entries = [line.strip() for line in pgpassfile.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (UnicodeError, OSError) as exc:
        raise CloneMigrationBlocked("Private rehearsal password file unreadable.") from exc
    if len(entries) != 1 or not entries[0].startswith(f"{HOST}:{PORT}:{DATABASE}:{ROLE}:"):
        raise CloneMigrationBlocked("Password file does not point solely at the isolated rehearsal database.")
    return proof


def sanitized_env(pgpassfile: Path) -> dict[str, str]:
    result = {key: val for key, val in os.environ.items()
              if not key.startswith("PG") and key not in ("DATABASE_URL", "ALEMBIC_CONFIG", "PYTHONPATH")}
    result["PGPASSFILE"] = str(pgpassfile)
    result["PGSSLMODE"] = "disable"
    result["DATABASE_URL"] = f"postgresql+psycopg://{ROLE}@{HOST}:{PORT}/{DATABASE}"
    return result


def run_command(args: list[str], *, env: dict, cwd: Path | None = None,
                runner: Callable = subprocess.run) -> str:
    try:
        response = runner(args, env=env, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, timeout=1800,
                          check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CloneMigrationBlocked("Isolated verification or Alembic command failed.") from exc
    if response.returncode:
        raise CloneMigrationBlocked("Isolated verification or Alembic command returned an error.")
    return (response.stdout or "").strip()


def query(sql: str, *, pgpassfile: Path, runner: Callable) -> str:
    return run_command(
        ["psql", "-X", "-w", "-A", "-t", "-v", "ON_ERROR_STOP=1",
         "-h", HOST, "-p", str(PORT), "-U", ROLE, "-d", DATABASE, "-c", sql],
        env=sanitized_env(pgpassfile), runner=runner,
    )


def inspect_target(*, pgpassfile: Path, runner: Callable) -> dict:
    identity = query(
        "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
        "current_user, (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)",
        pgpassfile=pgpassfile, runner=runner,
    ).split("|")
    if identity != [DATABASE, HOST, str(PORT), ROLE, "f"]:
        raise CloneMigrationBlocked("Database identity/role is not the isolated non-superuser rehearsal target.")
    revision = query("SELECT version_num FROM public.alembic_version", pgpassfile=pgpassfile, runner=runner)
    tables = query(
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p')",
        pgpassfile=pgpassfile, runner=runner,
    )
    try:
        table_count = int(tables)
    except ValueError as exc:
        raise CloneMigrationBlocked("Cannot verify restored table count.") from exc
    counts = {}
    for name in UNCHANGED_TABLES:
        if not re.fullmatch(r"[a-z_]+", name):
            raise CloneMigrationBlocked("Invalid fixed table name.")
        value = query(f"SELECT COUNT(*) FROM public.{name}", pgpassfile=pgpassfile, runner=runner)
        try:
            counts[name] = int(value)
        except ValueError as exc:
            raise CloneMigrationBlocked("Cannot verify restored source row count.") from exc
    present = query(
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname IN (" +
        ",".join("'" + table + "'" for table in EXPECTED_TABLES) + ")",
        pgpassfile=pgpassfile, runner=runner,
    )
    return {"revision": revision, "table_count": table_count, "row_counts": counts,
            "new_tables": int(present)}


def run_clone_migration(*, evidence: Path, pgpassfile: Path, outfile: Path,
                        checkout: Path, python_path: Path,
                        runner: Callable = subprocess.run,
                        rehearsal_dir: Path = DEFAULT_REHEARSAL_DIR) -> dict:
    proof = check_inputs(evidence=evidence, pgpassfile=pgpassfile,
                         outfile=outfile, rehearsal_dir=rehearsal_dir)
    if (checkout.is_symlink() or not checkout.is_dir()
        or not (checkout / "apps/api/alembic.ini").is_file()
        or not (checkout / "database/migrations/versions/0172_catinat_transfers.py").is_file()):
        raise CloneMigrationBlocked("Reviewed release candidate unavailable.")
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise CloneMigrationBlocked("Expected release candidate Python interpreter unavailable.")
    before = inspect_target(pgpassfile=pgpassfile, runner=runner)
    if (before["revision"] != SOURCE_REVISION or before["new_tables"] != 0
        or before["table_count"] != proof["RESTORED_TABLES"]):
        raise CloneMigrationBlocked("Restored source revision/tables do not match backup. No migration attempted.")

    env = sanitized_env(pgpassfile)
    env["PYTHONPATH"] = str(checkout / "apps/api")
    # The migration source is bound to the fixed isolated DATABASE_URL above.
    run_command(
        [str(python_path), "-m", "alembic", "-c",
         str(checkout / "apps/api/alembic.ini"), "upgrade", TARGET_REVISION],
        env=env, cwd=checkout / "apps/api", runner=runner,
    )
    after = inspect_target(pgpassfile=pgpassfile, runner=runner)
    if (after["revision"] != TARGET_REVISION or after["new_tables"] != len(EXPECTED_TABLES)
        or after["row_counts"] != before["row_counts"]):
        raise CloneMigrationBlocked("Migration postcheck failed: source rows or revision differ.")
    report = {
        "ISOLATED_MIGRATION": "PASS",
        "TARGET": f"{HOST}:{PORT}/{DATABASE}",
        "SOURCE_REVISION": SOURCE_REVISION,
        "TARGET_REVISION": TARGET_REVISION,
        "SOURCE_BACKUP_SHA256": MANAGED_BACKUP_SHA256,
        "PREVIOUS_TABLES": before["table_count"],
        "CREATED_CATINAT_TABLES": after["new_tables"],
        "UNCHANGED_ROW_COUNTS": before["row_counts"],
        "PRODUCTION_MIGRATION": "NOT_RUN",
    }
    fd = os.open(outfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True)
        handle.write("\n")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack-isolated-target", action="store_true")
    parser.add_argument("--source-evidence", type=Path, required=True)
    parser.add_argument("--pgpassfile", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.ack_isolated_target:
        parser.error("Explicit --ack-isolated-target is required.")
    try:
        report = run_clone_migration(
            evidence=args.source_evidence, pgpassfile=args.pgpassfile,
            outfile=args.result, checkout=args.checkout, python_path=args.python,
        )
    except CloneMigrationBlocked as exc:
        print("ISOLATED_MIGRATION=BLOCKED: " + str(exc))
        return 2
    print("ISOLATED_MIGRATION=PASS")
    print(f"SOURCE_REVISION={report['SOURCE_REVISION']}")
    print(f"TARGET_REVISION={report['TARGET_REVISION']}")
    print(f"CREATED_CATINAT_TABLES={report['CREATED_CATINAT_TABLES']}")
    print("PRODUCTION_MIGRATION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
