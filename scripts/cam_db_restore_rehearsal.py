#!/usr/bin/env python3
"""Restore one independently verified backup into an existing, empty, isolated DB.

Never creates/drops databases; never loads CAM production settings or credentials.
Must be run by an authorized operator from a trusted console, not from a job worker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

TARGET = re.compile(r"cam_restore_rehearsal_[a-z0-9_]{6,48}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MD5 = re.compile(r"[0-9a-f]{32}\Z")
SAFE_TABLE = re.compile(r"[a-z_][a-z0-9_]{1,62}\Z")
PRECHECK = (
    "SELECT current_database(), COALESCE(host(inet_server_addr()),''), "
    "(SELECT rolsuper FROM pg_roles WHERE rolname=current_user), "
    "(SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
    "AND n.nspname NOT LIKE 'pg_toast%' "
    "AND c.relkind IN ('r','p','v','m','f','S'));"
)
POSTCHECK = (
    "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
    "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p');"
)


class RestoreGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Request:
    dump: Path
    sha256: str
    md5: str
    database: str
    user: str
    port: int = 55432
    evidence: Path | None = None
    required_tables: tuple[str, ...] = ()
    # Independent operator-supplied counts for the *same backup snapshot*.
    # This tool verifies equality, not the provenance of source counts.
    expected_table_counts: tuple[tuple[str, int], ...] = ()


def _run(argv: list[str], *, runner: Callable = subprocess.run, capture: bool = False):
    # Do not put credentials in argv, stdout or exception strings.
    # libpq accepts inherited PGHOSTADDR/PGSERVICE that can silently override
    # host arguments. Keep only explicit password sourcing for the isolated DB.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    for key in ("PGPASSWORD", "PGPASSFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PGSSLMODE"] = "disable"
    try:
        result = runner(argv, env=env,
                        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RestoreGateError("Database restore tool unavailable or timed out.") from exc
    if result.returncode:
        raise RestoreGateError("PostgreSQL verification/restore command failed.")
    return (result.stdout or b"").decode("utf-8", errors="replace").strip() if capture else ""


def _psql(request: Request, sql: str, runner: Callable) -> str:
    return _run(
        ["psql", "-X", "-w", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
         "-h", "127.0.0.1", "-p", str(request.port),
         "-U", request.user, "-d", request.database, "-c", sql],
        runner=runner, capture=True,
    )


def _checksums(path: Path) -> tuple[str, str]:
    sha = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest(), md5.hexdigest()


def _parse_expected_table_counts(values: Sequence[str]) -> tuple[tuple[str, int], ...]:
    counts = []
    for item in values:
        name, separator, raw = item.partition("=")
        if (
            not separator or not SAFE_TABLE.fullmatch(name)
            or not re.fullmatch(r"[0-9]{1,19}", raw)
        ):
            raise RestoreGateError("Invalid expected table count specification.")
        value = int(raw)
        if value > 9223372036854775807:
            raise RestoreGateError("Expected table count exceeds supported range.")
        counts.append((name, value))
    return tuple(counts)


def verify_request(request: Request) -> int:
    if not TARGET.fullmatch(request.database):
        raise RestoreGateError("Target must be named cam_restore_rehearsal_*.")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{1,62}", request.user):
        raise RestoreGateError("Use a dedicated, non-superuser PostgreSQL role.")
    if not isinstance(request.port, int) or request.port < 1 or request.port > 65535 or request.port == 5432:
        raise RestoreGateError("Use an isolated PostgreSQL listener (not production port 5432).")
    if len(request.required_tables) > 20 or len(set(request.required_tables)) != len(request.required_tables):
        raise RestoreGateError("Require 1-20 distinct table identities.")
    if any(not SAFE_TABLE.fullmatch(name) for name in request.required_tables):
        raise RestoreGateError("Invalid required table identity.")
    expected = request.expected_table_counts
    expected_names = [name for name, _ in expected]
    if (
        len(expected) > 20 or len(set(expected_names)) != len(expected)
        or any(not SAFE_TABLE.fullmatch(name) or type(count) is not int
               or count < 0 or count > 9223372036854775807
               for name, count in expected)
    ):
        raise RestoreGateError("Invalid expected table counts.")
    if expected and set(expected_names) != set(request.required_tables):
        raise RestoreGateError("Expected counts must cover every required table.")
    if not SHA256.fullmatch(request.sha256) or not MD5.fullmatch(request.md5):
        raise RestoreGateError("Both independent expected SHA-256 and MD5 are required.")
    file = request.dump
    if not file.is_absolute() or file.is_symlink() or file.suffix != ".dump":
        raise RestoreGateError("Backup must be an absolute, nonsymlink .dump file.")
    try:
        metadata = file.stat()
    except OSError as exc:
        raise RestoreGateError("Backup file is missing.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise RestoreGateError("Backup must be a nonempty regular file.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RestoreGateError("Backup file must not be accessible by group/others.")
    if _checksums(file) != (request.sha256, request.md5):
        raise RestoreGateError("Backup checksum mismatch; no database commands issued.")
    if request.evidence is not None:
        if not request.evidence.is_absolute() or request.evidence.is_symlink() or request.evidence.exists():
            raise RestoreGateError("Evidence path must be a new, absolute nonsymlink file.")
        if not request.evidence.parent.is_dir():
            raise RestoreGateError("Evidence directory does not exist.")
    return metadata.st_size


def rehearse(request: Request, *, runner: Callable = subprocess.run) -> dict:
    size = verify_request(request)
    # The database must already exist, be empty, and have a non-superuser owner.
    # Never create, drop or alter a production database.
    parts = _psql(request, PRECHECK, runner).split("|")
    if len(parts) != 4 or parts[0] != request.database or parts[1] != "127.0.0.1":
        raise RestoreGateError("Connected PostgreSQL identity is not the expected local target.")
    if parts[2] != "f" or parts[3] != "0":
        raise RestoreGateError("Restore target is nonempty or role is superuser.")
    _run(["pg_restore", "--list", str(request.dump)], runner=runner)
    _run(
        ["pg_restore", "--no-owner", "--no-privileges", "--no-tablespaces",
         "--no-publications", "--no-subscriptions", "--no-security-labels",
         "--single-transaction", "--exit-on-error", "-w",
         "-h", "127.0.0.1", "-p", str(request.port), "-U", request.user,
         "-d", request.database, str(request.dump)],
        runner=runner,
    )
    try:
        tables = int(_psql(request, POSTCHECK, runner))
    except ValueError as exc:
        raise RestoreGateError("Restored schema validation failed.") from exc
    if tables <= 0:
        raise RestoreGateError("Restore completed but no user tables were found.")
    # Optional, exact allowlisted public-table checks run only against the
    # isolated restore, never the source database. No row content is emitted.
    verified_counts = []
    expected_by_table = dict(request.expected_table_counts)
    for table in request.required_tables:
        try:
            count = int(_psql(
                request, f'SELECT COUNT(*) FROM public."{table}";', runner,
            ))
        except ValueError as exc:
            raise RestoreGateError("Required restore table count invalid.") from exc
        if count < 0:
            raise RestoreGateError("Required restore table count invalid.")
        if expected_by_table and count != expected_by_table[table]:
            # Never emit the table name or source/restore counts in errors.
            raise RestoreGateError("Restored count differs from independent expected count.")
        verified_counts.append(count)
    result = {
        "RESTORE_REHEARSAL": "PASS", "FILE_MATCHES_EXPECTED_CHECKSUMS": True,
        "RESTORE_TARGET": request.database, "TARGET_HOST": "127.0.0.1",
        "BACKUP_SHA256": request.sha256, "BACKUP_MD5": request.md5,
        "BACKUP_SIZE_BYTES": size, "RESTORED_TABLES": tables,
        "REHEARSAL_DATABASE_VALIDATED": True,
        "REQUIRED_TABLE_COUNTS_VERIFIED": bool(request.required_tables),
        "REQUIRED_TABLE_COUNT": len(verified_counts),
        "EXPECTED_TABLE_COUNTS_MATCHED": bool(expected_by_table),
        "EXPECTED_COUNTS_PROVENANCE": (
            "OPERATOR_SUPPLIED_UNVERIFIED" if expected_by_table else "NOT_SUPPLIED"
        ),
    }
    if request.evidence is not None:
        descriptor = os.open(request.evidence, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(result, handle, sort_keys=True)
            handle.write("\n")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--md5", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-port", type=int, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--require-table", action="append", default=[],
                        help="Exact public table to verify on isolated restore (repeatable).")
    parser.add_argument("--expect-table-count", action="append", default=[],
                        metavar="TABLE=COUNT",
                        help="Operator-supplied row count at backup snapshot (repeatable).")
    parser.add_argument("--ack-isolated-target", action="store_true")
    args = parser.parse_args(argv)
    if not args.ack_isolated_target:
        parser.error("--ack-isolated-target is mandatory; use only an isolated empty database")
    try:
        expected = _parse_expected_table_counts(args.expect_table_count)
        result = rehearse(Request(args.dump, args.sha256, args.md5, args.database,
                                  args.db_user, args.db_port, args.evidence,
                                  tuple(args.require_table), expected))
    except RestoreGateError as exc:
        print(json.dumps({"RESTORE_REHEARSAL": "FAIL", "REASON": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
