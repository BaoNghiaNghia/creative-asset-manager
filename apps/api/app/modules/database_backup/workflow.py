from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.database_backup.google_drive import GoogleDriveDatabaseBackupStorage, RemoteDatabaseBackup
from app.modules.database_backup.retention import DatabaseBackupRetentionResult, DatabaseBackupRetentionService
from app.modules.database_backup.service import DatabaseBackupService, VerifiedDatabaseBackup


class LocalBackupService(Protocol):

    def exclusive_lock(self):
        ...

    def create_verified_dump_locked(self) -> VerifiedDatabaseBackup:
        ...

    def cleanup_verified_backup(self, backup: VerifiedDatabaseBackup) -> None:
        ...


@dataclass(frozen=True)
class DatabaseBackupWorkflowResult:
    backup: VerifiedDatabaseBackup
    remote: RemoteDatabaseBackup
    retention: DatabaseBackupRetentionResult


class DatabaseBackupWorkflow:

    def __init__(self, local_backup: DatabaseBackupService,
                 remote_storage: GoogleDriveDatabaseBackupStorage,
                 retention: DatabaseBackupRetentionService) -> None:
        self._local_backup = local_backup
        self._remote_storage = remote_storage
        self._retention = retention

    async def run(self) -> DatabaseBackupWorkflowResult:
        with self._local_backup.exclusive_lock():
            backup = self._local_backup.create_verified_dump_locked()
            uploaded = await self._remote_storage.upload(backup)
            verified = await self._remote_storage.verify(
                uploaded.remote_file_id, backup)
            try:
                retention = await self._retention.prune_after_verified_upload(
                    self._remote_storage,
                    protected_remote_file_id=verified.remote_file_id)
            finally:
                self._local_backup.cleanup_verified_backup(backup)
            return DatabaseBackupWorkflowResult(backup=backup,
                                                remote=verified,
                                                retention=retention)
