from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.modules.database_backup.google_drive import (
    DatabaseBackupRemoteError,
    GoogleDriveDatabaseBackupStorage,
    build_database_backup_storage,
)
from app.modules.database_backup.retention import DatabaseBackupRetentionService
from app.modules.database_backup.service import (
    DatabaseBackupAlreadyRunningError,
    DatabaseBackupConfigurationError,
    DatabaseBackupError,
    DatabaseBackupPreflightError,
    DatabaseBackupService,
)
from app.modules.database_backup.workflow import DatabaseBackupWorkflow


def verify_configuration(
    settings: Any,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Validate backup configuration without mutating PostgreSQL or Drive."""
    if not bool(getattr(settings, "DATABASE_BACKUP_ENABLED", False)):
        raise DatabaseBackupConfigurationError("Database backup is disabled.")
    folder_id = str(getattr(settings, "DATABASE_BACKUP_DRIVE_FOLDER_ID", "") or "").strip()
    if not folder_id:
        raise DatabaseBackupConfigurationError("Database backup Drive folder is not configured.")
    root_id = str(getattr(settings, "GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID", "") or "").strip()
    access_token = str(getattr(settings, "GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN", "") or "").strip()
    refresh_token = str(getattr(settings, "GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN", "") or "").strip()
    if not root_id or not (access_token or refresh_token):
        raise DatabaseBackupConfigurationError("Managed Google Drive storage is not configured.")
    if refresh_token and not (
        str(getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
        and str(getattr(settings, "GOOGLE_CLIENT_SECRET", "") or "").strip()
    ):
        raise DatabaseBackupConfigurationError("Managed Google Drive refresh credentials are incomplete.")
    database_url = str(getattr(settings, "DATABASE_URL", "") or "").strip()
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise DatabaseBackupConfigurationError("Database URL is invalid.") from exc
    if parsed.get_backend_name() != "postgresql" or not parsed.database:
        raise DatabaseBackupConfigurationError("Database backup requires PostgreSQL.")
    staging = Path(str(getattr(settings, "DATABASE_BACKUP_STAGING_DIRECTORY", "")))
    if not staging.is_absolute():
        raise DatabaseBackupConfigurationError("Database backup staging directory must be absolute.")
    if int(getattr(settings, "DATABASE_BACKUP_MIN_FREE_BYTES", 0)) <= 0:
        raise DatabaseBackupConfigurationError("Database backup free-space floor is invalid.")
    if int(getattr(settings, "DATABASE_BACKUP_RETENTION_DAYS", 0)) <= 0:
        raise DatabaseBackupConfigurationError("Database backup retention is invalid.")
    if int(getattr(settings, "DATABASE_BACKUP_MAX_FILES", 0)) <= 0:
        raise DatabaseBackupConfigurationError("Database backup file cap is invalid.")
    missing = [command for command in ("pg_dump", "pg_restore") if which(command) is None]
    if missing:
        raise DatabaseBackupConfigurationError("PostgreSQL backup executables are unavailable.")
    return {
        "DATABASE_BACKUP_ENABLED": "YES",
        "DATABASE_BACKUP_CONFIGURATION": "VALID",
        "MANAGED_GOOGLE_DRIVE_IDENTITY": "CONFIGURED",
        "TENANT_SOURCE_DRIVE_OAUTH": "NOT_USED",
        "RETENTION_DAYS": int(settings.DATABASE_BACKUP_RETENTION_DAYS),
        "MAX_FILES": int(settings.DATABASE_BACKUP_MAX_FILES),
    }


def build_components(settings: Any) -> tuple[
    DatabaseBackupService,
    GoogleDriveDatabaseBackupStorage,
    DatabaseBackupRetentionService,
]:
    local = DatabaseBackupService(settings)
    try:
        remote = build_database_backup_storage(settings)
    except (ValueError, DatabaseBackupRemoteError) as exc:
        raise DatabaseBackupConfigurationError("Managed backup storage is unavailable.") from exc
    retention = DatabaseBackupRetentionService(
        retention_days=int(settings.DATABASE_BACKUP_RETENTION_DAYS),
        max_files=int(settings.DATABASE_BACKUP_MAX_FILES),
    )
    return local, remote, retention


async def run_backup(settings: Any) -> dict[str, Any]:
    verify_configuration(settings)
    local, remote, retention = build_components(settings)
    result = await DatabaseBackupWorkflow(local, remote, retention).run()
    return {
        "BACKUP_STARTED": "YES",
        "STORAGE_PREFLIGHT": "PASS",
        "PG_DUMP": "PASS",
        "BACKUP_FILE_SIZE_BYTES": result.backup.size_bytes,
        "BACKUP_SHA256": result.backup.sha256,
        "DUMP_VERIFY": "PASS",
        "DRIVE_UPLOAD": "PASS",
        "REMOTE_VERIFY": "PASS",
        "REMOTE_FILE_ID": result.remote.remote_file_id,
        "REMOTE_BACKUP_COUNT_BEFORE_PRUNE": result.retention.remote_backup_count_before_prune,
        "AGE_PRUNED": result.retention.age_pruned,
        "COUNT_PRUNED": result.retention.count_pruned,
        "REMOTE_BACKUP_COUNT": result.retention.remote_backup_count,
        "LOCAL_CLEANUP": "PASS",
        "DATABASE_BACKUP": "SUCCESS",
    }


async def list_backups(settings: Any) -> dict[str, Any]:
    verify_configuration(settings)
    _local, remote, _retention = build_components(settings)
    backups = await remote.list_managed_backups()
    return {
        "READ_ONLY": "YES",
        "REMOTE_BACKUP_COUNT": len(backups),
        "BACKUPS": [
            {
                "remote_file_id": item.remote_file_id,
                "name": item.name,
                "size_bytes": item.size_bytes,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in sorted(
                backups,
                key=lambda item: item.created_at.isoformat() if item.created_at else "",
                reverse=True,
            )
        ],
    }


async def prune_backups(settings: Any) -> dict[str, Any]:
    verify_configuration(settings)
    _local, remote, retention = build_components(settings)
    result = await retention.prune_after_verified_upload(
        remote,
        protected_remote_file_id="",
    )
    return {
        "REMOTE_BACKUP_COUNT_BEFORE_PRUNE": result.remote_backup_count_before_prune,
        "AGE_PRUNED": result.age_pruned,
        "COUNT_PRUNED": result.count_pruned,
        "REMOTE_BACKUP_COUNT": result.remote_backup_count,
        "DATABASE_BACKUP_PRUNE": "SUCCESS",
    }


def _print_result(result: dict[str, Any]) -> None:
    for key, value in result.items():
        if isinstance(value, (list, dict)):
            print(f"{key}={json.dumps(value, separators=(',', ':'), sort_keys=True)}")
        else:
            print(f"{key}={value}")


def _print_failure(error: Exception) -> None:
    if isinstance(error, DatabaseBackupAlreadyRunningError):
        print("BACKUP_ALREADY_RUNNING=YES")
        print("NEW_BACKUP_STARTED=NO")
    elif isinstance(error, DatabaseBackupPreflightError):
        print("BACKUP_STARTED=NO")
        print("STORAGE_PREFLIGHT=FAIL")
    else:
        print("DATABASE_BACKUP=FAILED")
    print(f"ERROR_TYPE={type(error).__name__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Managed PostgreSQL backup operations")
    parser.add_argument("command", choices=("verify-config", "backup", "list", "prune"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        if arguments.command == "verify-config":
            result = verify_configuration(settings)
        elif arguments.command == "backup":
            result = asyncio.run(run_backup(settings))
        elif arguments.command == "list":
            result = asyncio.run(list_backups(settings))
        else:
            result = asyncio.run(prune_backups(settings))
        _print_result(result)
        return 0
    except (DatabaseBackupError, DatabaseBackupRemoteError, ValueError) as error:
        _print_failure(error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
