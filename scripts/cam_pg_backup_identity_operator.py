#!/usr/bin/env python3
"""Operator-only G0.1: safely provision a PostgreSQL logical-backup reader.

Default --status is read-only. --apply is an intentional production DDL action
and only creates a dedicated role and backup-only root-owned credential file.
Never prints, commits, or logs a secret. Does not start a full dump or modify
CAM application role privileges. Requires root access on the database host.
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

PRODUCTION_ENV = Path("/etc/creative-asset-manager/production.env")
BACKUP_ENV = Path("/etc/creative-asset-manager/database-backup.env")
DATABASE = "creative_asset_manager"
APP_ROLE = "cam_app"
BACKUP_ROLE = "cam_backup"


class BackupIdentityGateError(RuntimeError):
    """Fail closed; messages never include connection strings or credentials."""


def load_target(path: Path = PRODUCTION_ENV) -> Any:
    """Read only the app DB endpoint from the protected systemd environment."""
    try:
        attrs = path.lstat()
        if (
            not stat.S_ISREG(attrs.st_mode)
            or attrs.st_uid != 0
            or (stat.S_IMODE(attrs.st_mode) & 0o007)
        ):
            raise ValueError("untrusted environment file")
        values = dotenv_values(path, interpolate=False)
        url = make_url(str(values.get("DATABASE_URL") or ""))
        if (
            url.get_backend_name() != "postgresql"
            or url.database != DATABASE
            or url.username != APP_ROLE
            or not url.host
            or url.query
        ):
            raise ValueError("unexpected application database endpoint")
        return url
    except Exception as exc:
        raise BackupIdentityGateError(
            "Protected production database configuration is unavailable or unexpected."
        ) from exc


def postgres_sql(sql: str, *, runner=subprocess.run) -> str:
    """Run against the fixed database via the local OS postgres peer identity.

    SQL is sent exclusively on stdin; stderr is suppressed from tool outputs,
    even on failure, because CREATE ROLE carries a generated credential.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    try:
        result = runner(
            ["runuser", "-u", "postgres", "--", "psql", "-X", "-A", "-t",
             "-v", "ON_ERROR_STOP=1", "-d", DATABASE, "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=45, env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupIdentityGateError("PostgreSQL operator connection failed.") from exc
    if result.returncode != 0:
        raise BackupIdentityGateError(
            "PostgreSQL operation failed. Inspect protected server logs privately."
        )
    return result.stdout.strip()


def role_exists(*, sql_runner=postgres_sql) -> bool:
    result = sql_runner(
        "SELECT count(*) FROM pg_roles WHERE rolname = 'cam_backup';"
    ).strip()
    if result not in {"0", "1"}:
        raise BackupIdentityGateError("Unexpected role lookup response.")
    return result == "1"


def privilege_coverage(*, sql_runner=postgres_sql) -> tuple[int, int, int, int, int, int]:
    """Read-only catalog audit; separate reader must access ALL user data."""
    # MATERIALIZED is essential: PostgreSQL can otherwise evaluate
    # has_sequence_privilege() on pg_toast/table OIDs before relkind filters.
    query = """
WITH
  table_objects AS MATERIALIZED (
    SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p','m')
  ),
  sequence_objects AS MATERIALIZED (
    SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='S'
  )
SELECT
  (SELECT COUNT(*) FROM table_objects),
  (SELECT COUNT(*) FROM table_objects
   WHERE NOT has_table_privilege('cam_backup',oid,'SELECT')),
  (SELECT COUNT(*) FROM sequence_objects),
  (SELECT COUNT(*) FROM sequence_objects
   WHERE NOT has_sequence_privilege('cam_backup',oid,'SELECT')),
  (SELECT COUNT(*) FROM table_objects
   WHERE has_table_privilege('cam_backup',oid,'INSERT')
      OR has_table_privilege('cam_backup',oid,'UPDATE')
      OR has_table_privilege('cam_backup',oid,'DELETE')
      OR has_table_privilege('cam_backup',oid,'TRUNCATE')),
  (SELECT COUNT(*) FROM sequence_objects
   WHERE has_sequence_privilege('cam_backup',oid,'USAGE')
      OR has_sequence_privilege('cam_backup',oid,'UPDATE'));
"""
    raw = sql_runner(query).split("|")
    if len(raw) != 6:
        raise BackupIdentityGateError("Unexpected privilege audit response.")
    try:
        return tuple(int(s) for s in raw)  # type: ignore[return-value]
    except ValueError as exc:
        raise BackupIdentityGateError("Invalid privilege audit counts.") from exc


def credentials_file_state(path: Path = BACKUP_ENV) -> str:
    try:
        data = path.lstat()
    except FileNotFoundError:
        return "MISSING"
    if not stat.S_ISREG(data.st_mode) or data.st_uid != 0 or stat.S_IMODE(data.st_mode) != 0o600:
        return "INVALID"
    return "ROOT_0600"


def endpoint_resolves(url: Any) -> bool:
    try:
        socket.getaddrinfo(url.host, url.port or 5432, type=socket.SOCK_STREAM)
        return True
    except (OSError, ValueError, TypeError):
        return False


def ddl_for_role(password: str) -> str:
    """Fixed scope, no SQL identifiers supplied by callers."""
    quoted = "'" + password.replace("'", "''") + "'"
    return f"""
BEGIN;
CREATE ROLE cam_backup WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOINHERIT PASSWORD {quoted};
GRANT CONNECT ON DATABASE creative_asset_manager TO cam_backup;
GRANT USAGE ON SCHEMA public TO cam_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cam_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO cam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE cam_app IN SCHEMA public
  GRANT SELECT ON TABLES TO cam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT ON TABLES TO cam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE cam_app IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO cam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO cam_backup;
COMMIT;
"""


def write_backup_file(path: Path, backup_url: str) -> None:
    # Exclusive, nonsymlink file creation; never overwrite existing secrets.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("DATABASE_BACKUP_DATABASE_URL=" + backup_url + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def verify_readonly_connection(url: Any, secret: str, *, runner=subprocess.run) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update({
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGUSER": BACKUP_ROLE,
        "PGPASSWORD": secret,
        "PGDATABASE": DATABASE,
    })
    try:
        result = runner(
            ["psql", "-X", "-w", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
             "-c", "SELECT current_user, current_database();"],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupIdentityGateError("Dedicated backup connection unavailable.") from exc
    if result.returncode != 0 or result.stdout.strip() != "cam_backup|creative_asset_manager":
        raise BackupIdentityGateError("Dedicated backup login verification failed.")


def run_status(url: Any, *, sql_runner=postgres_sql, backup_path: Path = BACKUP_ENV) -> dict[str, str | int]:
    found = role_exists(sql_runner=sql_runner)
    result: dict[str, str | int] = {
        "DATABASE": DATABASE,
        "BACKUP_ROLE": "EXISTS" if found else "MISSING",
        "BACKUP_ENV": credentials_file_state(backup_path),
        "APP_ENDPOINT_DNS": "OK" if endpoint_resolves(url) else "UNRESOLVED",
        "FULL_DUMP_STARTED": "NO",
    }
    if found:
        tables, missing_tables, sequences, missing_seq, write_tables, write_seq = privilege_coverage(
            sql_runner=sql_runner
        )
        result.update({
            "TABLES": tables,
            "TABLES_WITHOUT_SELECT": missing_tables,
            "SEQUENCES": sequences,
            "SEQUENCES_WITHOUT_SELECT": missing_seq,
            "TABLES_WITH_WRITE": write_tables,
            "SEQUENCES_WITH_WRITE": write_seq,
        })
    return result


def verify_existing(
    url: Any, *, sql_runner=postgres_sql, backup_path: Path = BACKUP_ENV,
) -> dict[str, str | int]:
    """Read-only verification of an already-provisioned backup credential."""
    if not role_exists(sql_runner=sql_runner):
        raise BackupIdentityGateError("Dedicated backup role does not exist.")
    if credentials_file_state(backup_path) != "ROOT_0600":
        raise BackupIdentityGateError("Backup-only credential file missing or insecure.")
    try:
        configured = make_url(
            str(dotenv_values(backup_path, interpolate=False).get("DATABASE_BACKUP_DATABASE_URL") or "")
        )
        if (
            configured.get_backend_name() != "postgresql"
            or configured.database != DATABASE
            or configured.host != url.host
            or (configured.port or 5432) != (url.port or 5432)
            or configured.username != BACKUP_ROLE
            or not configured.password
            or configured.query
        ):
            raise ValueError("invalid backup connection")
    except Exception as exc:
        raise BackupIdentityGateError(
            "Backup-only credential file has an unexpected connection target."
        ) from exc
    counts = privilege_coverage(sql_runner=sql_runner)
    if counts[0] <= 0 or counts[1] or counts[3] or counts[4] or counts[5]:
        raise BackupIdentityGateError("Backup role is not strictly read-only on every user object.")
    verify_readonly_connection(url, configured.password)
    return run_status(url, sql_runner=sql_runner, backup_path=backup_path)


def provision(url: Any, *, sql_runner=postgres_sql, backup_path: Path = BACKUP_ENV) -> dict[str, str | int]:
    if os.geteuid() != 0:
        raise BackupIdentityGateError("Only a local root operator can provision backup credentials.")
    if role_exists(sql_runner=sql_runner):
        raise BackupIdentityGateError("Backup role already exists. Refusing to rotate or alter it.")
    if credentials_file_state(backup_path) != "MISSING":
        raise BackupIdentityGateError("Backup credential file exists or is unsafe. Refusing overwrite.")
    if not endpoint_resolves(url):
        raise BackupIdentityGateError("Application database hostname is not resolvable on this host.")
    if backup_path.parent.is_symlink() or not backup_path.parent.is_dir():
        raise BackupIdentityGateError("Protected backup configuration directory is unsafe.")
    secret = secrets.token_urlsafe(48)
    backup_url = url.set(username=BACKUP_ROLE, password=secret).render_as_string(
        hide_password=False
    )
    try:
        write_backup_file(backup_path, backup_url)
        sql_runner(ddl_for_role(secret))
    except BaseException:
        # Only a failed transaction removes its newly-created credential file.
        # If SQL completed, preserve the credential for safe manual recovery.
        if not role_exists(sql_runner=sql_runner):
            backup_path.unlink(missing_ok=True)
        raise
    counts = privilege_coverage(sql_runner=sql_runner)
    if counts[0] <= 0 or counts[1] or counts[3] or counts[4] or counts[5]:
        raise BackupIdentityGateError("Backup role privilege audit failed; no full dump attempted.")
    verify_readonly_connection(url, secret)
    result = run_status(url, sql_runner=sql_runner, backup_path=backup_path)
    if result["BACKUP_ENV"] != "ROOT_0600":
        raise BackupIdentityGateError("Backup credential file ownership/mode not secure.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="CAM PostgreSQL backup identity operator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Read-only status")
    group.add_argument("--verify", action="store_true", help="Verify saved role and login read-only")
    group.add_argument("--apply", action="store_true", help="Provision production backup reader")
    args = parser.parse_args()
    try:
        url = load_target()
        info = provision(url) if args.apply else verify_existing(url) if args.verify else run_status(url)
    except BackupIdentityGateError as exc:
        print("BACKUP_IDENTITY=BLOCKED")
        print("ERROR=" + str(exc))
        return 2
    for key, value in info.items():
        print(f"{key}={value}")
    print("BACKUP_IDENTITY=" + ("PROVISIONED" if args.apply else "VERIFIED" if args.verify else "INSPECTED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
