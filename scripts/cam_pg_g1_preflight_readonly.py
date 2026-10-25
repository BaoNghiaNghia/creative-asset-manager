#!/usr/bin/env python3
"""Read-only CAM PostgreSQL G1 preflight; never restores or changes production.

This validates the application's local receipt (not a new independent remote
download), estimates capacity for a same-volume isolated restore and reports
which hard gates still need operator-side evidence. It never outputs secrets.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from app.modules.database_backup.verified_receipt import has_recent_verified_receipt

GIB = 1024**3
DB_NAME = "creative_asset_manager"
RECEIPT_DIRECTORY = "/var/lib/creative-asset-manager/database-backup"


def evaluate(
    *, receipt_ok: bool, db_bytes: int, free_bytes: int,
    isolated_cluster_count: int,
) -> dict[str, object]:
    """Conservative same-volume budget; never infer restore success."""
    if db_bytes <= 0 or free_bytes < 0 or isolated_cluster_count < 0:
        raise ValueError("invalid preflight inputs")
    reserve = 8 * GIB
    # Heuristic, not a precise pg_restore disk estimate:
    # 150% of live DB size for restored heap/index/WAL and 1 GiB safety buffer.
    additional = (3 * db_bytes + 1) // 2 + GIB
    sufficient = free_bytes >= reserve + additional
    return {
        "read_only": True,
        "backup_local_recent_receipt": "PASS" if receipt_ok else "BLOCKED",
        "remote_independent_redownload_checksum": "NOT_VERIFIED",
        "isolated_postgresql_cluster": (
            "PRESENT_UNVERIFIED" if isolated_cluster_count else "NOT_PRESENT"
        ),
        "isolated_restore_rehearsal": "NOT_VERIFIED",
        "production_database_bytes": db_bytes,
        "root_volume_free_bytes": free_bytes,
        "same_volume_estimated_additional_bytes": additional,
        "minimum_free_remaining_bytes": reserve,
        "same_volume_restore_capacity": "SUFFICIENT_ESTIMATE" if sufficient else "BLOCKED",
        "g1_recovery": "BLOCKED",
        "g2_bulk_maintenance": "NOT_EVALUATED",
        "production_mutations": "NONE",
    }


def _command(args: list[str]) -> str:
    environment = dict(os.environ)
    environment["PGOPTIONS"] = (
        "-c default_transaction_read_only=on "
        "-c statement_timeout=6000 -c lock_timeout=1000"
    )
    result = subprocess.run(
        args, capture_output=True, text=True, check=False,
        timeout=12, env=environment,
    )
    if result.returncode:
        raise RuntimeError("read-only prerequisite command failed")
    return result.stdout


def _db_bytes() -> int:
    output = _command([
        "runuser", "-u", "postgres", "--", "psql",
        "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", DB_NAME,
        "-c", "SELECT pg_database_size(current_database());",
    ]).strip()
    return int(output)


def _other_cluster_count() -> int:
    lines = _command(["pg_lsclusters"]).splitlines()
    count = 0
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "16" and parts[2] != "5432":
            count += 1
    return count


def main() -> int:
    if os.geteuid() != 0:
        print(json.dumps({"g1_recovery": "BLOCKED", "reason": "operator_root_required"}))
        return 2
    try:
        receipt = has_recent_verified_receipt(RECEIPT_DIRECTORY)
        bytes_database = _db_bytes()
        free_bytes = shutil.disk_usage(Path("/")).free
        others = _other_cluster_count()
        result = evaluate(
            receipt_ok=receipt, db_bytes=bytes_database,
            free_bytes=free_bytes, isolated_cluster_count=others,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        print(json.dumps({"g1_recovery": "BLOCKED", "reason": "preflight_unavailable"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
