from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, RemoteDatabaseBackup


class ManagedBackupStorage(Protocol):
    folder_id: str

    async def list_managed_backups(self) -> list[RemoteDatabaseBackup]:
        ...

    async def delete_managed_backup(self,
                                    remote: RemoteDatabaseBackup) -> None:
        ...


@dataclass(frozen=True)
class DatabaseBackupRetentionResult:
    remote_backup_count_before_prune: int
    age_pruned: int
    count_pruned: int
    remote_backup_count: int


class DatabaseBackupRetentionService:

    def __init__(self,
                 *,
                 retention_days: int = 21,
                 max_files: int = 6) -> None:
        if retention_days <= 0:
            raise ValueError("database backup retention days must be positive")
        if max_files <= 0:
            raise ValueError(
                "database backup maximum file count must be positive")
        self._retention_days = retention_days
        self._max_files = max_files

    async def prune_after_verified_upload(
            self,
            storage: ManagedBackupStorage,
            *,
            protected_remote_file_id: str,
            now: datetime | None = None) -> DatabaseBackupRetentionResult:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError(
                "database backup retention requires an aware timestamp")
        backups = await storage.list_managed_backups()
        managed = [
            backup for backup in backups if storage.folder_id in backup.parents
            and backup.app_properties.get("cam_kind") == DATABASE_BACKUP_KIND
        ]
        threshold = current_time - timedelta(days=self._retention_days)
        age_candidates = [
            backup for backup in managed
            if backup.remote_file_id != protected_remote_file_id
            and backup.created_at is not None and backup.created_at < threshold
        ]
        for backup in age_candidates:
            await storage.delete_managed_backup(backup)
        remaining = [
            backup for backup in managed if backup.remote_file_id not in
            {item.remote_file_id
             for item in age_candidates}
        ]
        ordered = sorted(remaining, key=self._newest_first_key, reverse=True)
        count_candidates = ordered[self._max_files:]
        for backup in count_candidates:
            if backup.remote_file_id == protected_remote_file_id:
                continue
            await storage.delete_managed_backup(backup)
        count_pruned = sum(backup.remote_file_id != protected_remote_file_id
                           for backup in count_candidates)
        return DatabaseBackupRetentionResult(
            remote_backup_count_before_prune=len(managed),
            age_pruned=len(age_candidates),
            count_pruned=count_pruned,
            remote_backup_count=len(remaining) - count_pruned)

    @staticmethod
    def _newest_first_key(backup: RemoteDatabaseBackup) -> datetime:
        return backup.created_at or datetime.min.replace(tzinfo=timezone.utc)
