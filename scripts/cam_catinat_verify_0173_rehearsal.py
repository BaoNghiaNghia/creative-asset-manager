#!/usr/bin/env python3
"""Read-only audit of Catinat's already-migrated *isolated* PostgreSQL clone.

Never executes Alembic upgrade, never reads production.env and never writes
Google Sheets. Only creates one immutable private evidence file after PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from cam_catinat_migrate_restored_clone import (
    CloneMigrationBlocked, DATABASE, HOST, PORT, ROLE,
    EXPECTED_TABLES, check_inputs, inspect_target, query,
)

EXPECTED_BRANDING_TABLES = (
    "bc_browser_observations",
    "bc_browser_observation_reviews",
)
TARGET_REVISION = "0173_catinat_branding_merge"
PRIOR_REVISION = "0172_catinat_transfers"
MERGE_PARENTS = {
    PRIOR_REVISION,
    "0170_branding_dna_browser_observation_reviews",
}


def _read_private(path: Path) -> dict:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or stat.S_IMODE(s.st_mode) != 0o600 or s.st_uid != os.geteuid():
        raise CloneMigrationBlocked("Historical evidence must be private mode 0600.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CloneMigrationBlocked("Malformed migration evidence.")
    return data


def verify_previous_proof(previous: dict | None, current: dict) -> None:
    """Fail closed on changed evidence, allowing only a fresh audit timestamp."""
    if previous is not None and (
        previous.keys() != current.keys()
        or any(previous[key] != value for key, value in current.items()
               if key != "AUDITED_AT_UTC")
    ):
        raise CloneMigrationBlocked("Existing proof disagrees with current isolated read-only inspection.")


def audit(root: Path, *, write: bool = False) -> dict:
    root = root.resolve(strict=True)
    backup = root / "cam-db-20261020-164908+0700.dump"
    source_file = root / "restore-proof.json"
    prior_file = root / "migration-proof.json"
    pgpassfile = root / "rehearsal.pgpass"
    outfile = root / "migration-0173-proof.json"
    checkout = root / "release_candidate"
    # check_inputs is shared with the one-shot migration writer and rejects
    # existing output files. A read-only repeat audit must instead validate
    # using a fixed, nonexistent path and leave prior proof untouched.
    checked_outfile = outfile if write else root / ".catinat_0173_read_only_no_write"
    restore = check_inputs(evidence=source_file, pgpassfile=pgpassfile,
                           outfile=checked_outfile, rehearsal_dir=root)
    previous_audit = _read_private(outfile) if (not write and outfile.exists()) else None
    previous = _read_private(prior_file)
    if (previous.get("ISOLATED_MIGRATION") != "PASS"
        or previous.get("PRODUCTION_MIGRATION") != "NOT_RUN"
        or previous.get("SOURCE_REVISION") != "0168_branding_dna_outline_versions"
        or previous.get("TARGET_REVISION") != PRIOR_REVISION
        or previous.get("SOURCE_BACKUP_SHA256") != restore["BACKUP_SHA256"]
        or previous.get("PREVIOUS_TABLES") != restore["RESTORED_TABLES"]
        or previous.get("CREATED_CATINAT_TABLES") != len(EXPECTED_TABLES)):
        raise CloneMigrationBlocked("Historical source/0172 evidence mismatch.")
    if backup.is_symlink() or not backup.is_file() or backup.stat().st_size != restore["BACKUP_SIZE_BYTES"]:
        raise CloneMigrationBlocked("Backup file is absent, replaced or altered.")
    h = hashlib.sha256()
    with backup.open("rb") as fd:
        for chunk in iter(lambda: fd.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != restore["BACKUP_SHA256"]:
        raise CloneMigrationBlocked("Actual backup checksum differs from verified source.")
    migration_file = checkout / "database/migrations/versions/0173_merge_inventory_branding_heads.py"
    if migration_file.is_symlink() or not migration_file.is_file():
        raise CloneMigrationBlocked("Reviewed 0173 migration file missing.")
    spec = importlib.util.spec_from_file_location("catinat_rehearsal_merge", migration_file)
    if spec is None or spec.loader is None:
        raise CloneMigrationBlocked("Cannot inspect migration graph.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.revision != TARGET_REVISION or set(module.down_revision) != MERGE_PARENTS:
        raise CloneMigrationBlocked("0173 source migration ancestry mismatch.")
    current = inspect_target(pgpassfile=pgpassfile, runner=subprocess.run)
    if (current["revision"] != TARGET_REVISION
        or current["new_tables"] != len(EXPECTED_TABLES)
        or current["table_count"] != restore["RESTORED_TABLES"] + len(EXPECTED_TABLES) + len(EXPECTED_BRANDING_TABLES)
        or current["row_counts"] != previous.get("UNCHANGED_ROW_COUNTS")):
        raise CloneMigrationBlocked("Current revision/tables/source row counts invalid.")
    def q(sql: str) -> str:
        return query(sql, pgpassfile=pgpassfile, runner=subprocess.run)
    if q("SELECT count(*) FROM public.alembic_version") != "1":
        raise CloneMigrationBlocked("Unexpected Alembic state count.")
    for name in (*EXPECTED_TABLES, *EXPECTED_BRANDING_TABLES):
        if q(f"SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='{name}'") != "1":
            raise CloneMigrationBlocked(f"Required migration table missing: {name}")
    for name, kind in (
        ("fk_bc_browser_review_observation_scope", "f"),
        ("uq_bc_browser_observation_scope", "u"),
    ):
        if q(f"SELECT count(*) FROM pg_constraint WHERE conname='{name}' AND contype='{kind}'") != "1":
            raise CloneMigrationBlocked(f"Required scoped constraint missing: {name}")
    report = {
        "ISOLATED_0173_AUDIT": "PASS",
        "AUDIT_MODE": "READ_ONLY_DB",
        "AUDITED_AT_UTC": datetime.now(timezone.utc).isoformat(),
        "SOURCE_BACKUP_SHA256": restore["BACKUP_SHA256"],
        "RESTORE_REHEARSAL": restore["RESTORE_REHEARSAL"],
        "PRIOR_MIGRATION_TO_0172": previous["ISOLATED_MIGRATION"],
        "ISOLATED_DB": f"{HOST}:{PORT}/{DATABASE}",
        "ISOLATED_ROLE": ROLE,
        "ALEMBIC_CURRENT": current["revision"],
        "MERGE_PARENTS": sorted(MERGE_PARENTS),
        "RESTORED_TABLE_COUNT": restore["RESTORED_TABLES"],
        "CURRENT_TABLE_COUNT": current["table_count"],
        "CATINAT_TABLE_COUNT": len(EXPECTED_TABLES),
        "BRANDING_TABLE_COUNT": len(EXPECTED_BRANDING_TABLES),
        "SOURCE_ROW_COUNTS_UNCHANGED": True,
        "PRODUCTION_MIGRATION": "NOT_RUN_BY_THIS_AUDIT",
        "SHEETS_WRITES": 0,
    }
    # Repeated audits must not silently accept changed source or migration data.
    verify_previous_proof(previous_audit, report)
    if write:
        fd = os.open(outfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack-read-only-isolated", action="store_true")
    parser.add_argument("--write-private-proof", action="store_true")
    args = parser.parse_args()
    if not args.ack_read_only_isolated:
        parser.error("Explicit read-only rehearsal acknowledgement required.")
    try:
        print(json.dumps(audit(Path(__file__).resolve().parent,
                               write=args.write_private_proof), sort_keys=True))
    except (CloneMigrationBlocked, OSError, ValueError) as exc:
        print("ISOLATED_0173_AUDIT=BLOCKED: " + str(exc))
        raise SystemExit(2) from exc