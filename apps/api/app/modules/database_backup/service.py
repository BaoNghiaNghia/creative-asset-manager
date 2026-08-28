from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url


_HO_CHI_MINH = ZoneInfo("Asia/Ho_Chi_Minh")
_CHECKSUM_CHUNK_BYTES = 1024 * 1024


class DatabaseBackupError(RuntimeError):
    """Base exception for a local database backup failure."""


class DatabaseBackupConfigurationError(DatabaseBackupError):
    """The service cannot safely start with the supplied configuration."""


class DatabaseBackupPreflightError(DatabaseBackupError):
    """The staging directory cannot safely hold a backup."""


class DatabaseBackupAlreadyRunningError(DatabaseBackupError):
    """Another backup process owns the host-level lock."""


class DatabaseBackupDumpError(DatabaseBackupError):
    """pg_dump did not create a valid database dump."""


class DatabaseBackupVerificationError(DatabaseBackupError):
    """A dump failed its local verification."""


@dataclass(frozen=True)
class VerifiedDatabaseBackup:
    path: Path
    created_at: datetime
    size_bytes: int
    sha256: str
    md5_checksum: str


Runner = Callable[..., Any]


class DatabaseBackupService:
    """Create and locally verify a custom PostgreSQL dump."""

    def __init__(
        self,
        settings: Any,
        *,
        runner: Runner = subprocess.run,
        disk_usage: Callable[[str | os.PathLike[str]], SimpleNamespace] = shutil.disk_usage,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._disk_usage = disk_usage
        self._now = now or (lambda timezone: datetime.now(timezone))

    @property
    def staging_directory(self) -> Path:
        configured = str(
            getattr(self._settings, "DATABASE_BACKUP_STAGING_DIRECTORY", "")
        ).strip()
        if not configured:
            raise DatabaseBackupConfigurationError(
                "Database backup staging directory is not configured."
            )
        path = Path(configured)
        if not path.is_absolute():
            raise DatabaseBackupConfigurationError(
                "Database backup staging directory must be absolute."
            )
        return path

    @contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        staging = self._ensure_staging_directory()
        lock_path = staging / ".database-backup.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DatabaseBackupAlreadyRunningError(
                    "A database backup is already running."
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def create_verified_dump(self) -> VerifiedDatabaseBackup:
        """Create, verify, and checksum one local custom-format dump."""
        if not bool(getattr(self._settings, "DATABASE_BACKUP_ENABLED", False)):
            raise DatabaseBackupConfigurationError("Database backup is disabled.")
        with self.exclusive_lock():
            return self.create_verified_dump_locked()

    def create_verified_dump_locked(self) -> VerifiedDatabaseBackup:
        """Create a dump while the caller owns the exclusive lock.

        DB-BACKUP-2 keeps the lock through remote verification and retention so
        two host processes cannot interleave their local or Drive lifecycle.
        """
        if not bool(getattr(self._settings, "DATABASE_BACKUP_ENABLED", False)):
            raise DatabaseBackupConfigurationError("Database backup is disabled.")
        return self._create_verified_dump_locked()

    def cleanup_verified_backup(self, backup: VerifiedDatabaseBackup) -> None:
        """Safely remove a verified staging file after a later remote success."""
        staging = self.staging_directory.resolve()
        path = backup.path.resolve()
        if path.parent != staging:
            raise DatabaseBackupConfigurationError(
                "Refusing to clean a file outside database backup staging."
            )
        path.unlink(missing_ok=True)

    def _create_verified_dump_locked(self) -> VerifiedDatabaseBackup:
        staging = self._ensure_staging_directory()
        self._ensure_free_space(staging)
        created_at = self._now(_HO_CHI_MINH)
        filename = created_at.strftime("cam-db-%Y%m%d-%H%M%S%z.dump")
        destination = staging / filename
        if destination.exists():
            raise DatabaseBackupConfigurationError(
                "A database backup staging file already exists for this timestamp."
            )

        try:
            result = self._run_pg_dump(destination)
            if int(getattr(result, "returncode", 1)) != 0:
                raise DatabaseBackupDumpError("pg_dump failed.")
            destination.chmod(0o600)
            return self.verify_dump(destination, created_at=created_at)
        except DatabaseBackupError:
            self._cleanup_partial(destination)
            raise
        except (FileNotFoundError, PermissionError) as exc:
            self._cleanup_partial(destination)
            raise DatabaseBackupConfigurationError(
                "PostgreSQL backup executable is unavailable."
            ) from exc
        except OSError as exc:
            self._cleanup_partial(destination)
            raise DatabaseBackupDumpError("pg_dump could not be started.") from exc

    def verify_dump(
        self, path: Path, *, created_at: datetime | None = None
    ) -> VerifiedDatabaseBackup:
        if not path.is_file() or path.stat().st_size <= 0:
            raise DatabaseBackupVerificationError(
                "Database backup file is missing or empty."
            )
        try:
            result = self._runner(
                ["pg_restore", "--list", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise DatabaseBackupConfigurationError(
                "PostgreSQL restore executable is unavailable."
            ) from exc
        except OSError as exc:
            raise DatabaseBackupVerificationError(
                "pg_restore could not be started."
            ) from exc
        if int(getattr(result, "returncode", 1)) != 0:
            raise DatabaseBackupVerificationError("pg_restore verification failed.")
        size_bytes = path.stat().st_size
        sha256, md5_checksum = self._checksums(path)
        if path.stat().st_size != size_bytes:
            raise DatabaseBackupVerificationError(
                "Database backup changed while checksums were calculated."
            )
        return VerifiedDatabaseBackup(
            path=path,
            created_at=created_at or self._now(_HO_CHI_MINH),
            size_bytes=size_bytes,
            sha256=sha256,
            md5_checksum=md5_checksum,
        )

    def _run_pg_dump(self, destination: Path) -> Any:
        environment = self._postgres_environment()
        command = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(destination),
        ]
        return self._runner(
            command,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _postgres_environment(self) -> dict[str, str]:
        database_url = str(getattr(self._settings, "DATABASE_URL", "") or "").strip()
        if not database_url:
            raise DatabaseBackupConfigurationError("Database URL is not configured.")
        try:
            url = make_url(database_url)
        except Exception as exc:
            raise DatabaseBackupConfigurationError(
                "Database URL is not a valid PostgreSQL URL."
            ) from exc
        if url.get_backend_name() != "postgresql" or not url.database:
            raise DatabaseBackupConfigurationError(
                "Database backup requires a PostgreSQL database URL."
            )
        environment = os.environ.copy()
        environment["PGDATABASE"] = url.database
        if url.host:
            environment["PGHOST"] = url.host
        if url.port:
            environment["PGPORT"] = str(url.port)
        if url.username:
            environment["PGUSER"] = url.username
        if url.password is not None:
            environment["PGPASSWORD"] = str(url.password)
        return environment

    def _ensure_staging_directory(self) -> Path:
        staging = self.staging_directory
        staging.mkdir(parents=True, exist_ok=True, mode=0o750)
        if not staging.is_dir():
            raise DatabaseBackupConfigurationError(
                "Database backup staging path is not a directory."
            )
        staging.chmod(0o750)
        return staging

    def _ensure_free_space(self, staging: Path) -> None:
        minimum = int(
            getattr(self._settings, "DATABASE_BACKUP_MIN_FREE_BYTES", 0)
        )
        if minimum <= 0:
            raise DatabaseBackupConfigurationError(
                "Database backup minimum free space must be positive."
            )
        if int(self._disk_usage(staging).free) < minimum:
            raise DatabaseBackupPreflightError(
                "Insufficient free staging space for database backup."
            )

    @staticmethod
    def _checksums(path: Path) -> tuple[str, str]:
        sha256 = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as handle:
            while block := handle.read(_CHECKSUM_CHUNK_BYTES):
                sha256.update(block)
                md5.update(block)
        return sha256.hexdigest(), md5.hexdigest()

    @staticmethod
    def _cleanup_partial(path: Path) -> None:
        path.unlink(missing_ok=True)
