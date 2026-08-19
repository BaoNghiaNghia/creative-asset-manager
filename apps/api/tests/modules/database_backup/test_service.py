from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.config import Settings
from app.modules.database_backup.service import (
    DatabaseBackupAlreadyRunningError,
    DatabaseBackupConfigurationError,
    DatabaseBackupDumpError,
    DatabaseBackupPreflightError,
    DatabaseBackupService,
    DatabaseBackupVerificationError,
)


class FakeRunner:
    def __init__(
        self,
        *,
        dump_returncode: int = 0,
        restore_returncode: int = 0,
        dump_bytes: bytes = b"custom-dump",
        dump_error: Exception | None = None,
        restore_error: Exception | None = None,
    ) -> None:
        self.dump_returncode = dump_returncode
        self.restore_returncode = restore_returncode
        self.dump_bytes = dump_bytes
        self.dump_error = dump_error
        self.restore_error = restore_error
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))
        if command[0] == "pg_dump":
            if self.dump_error:
                raise self.dump_error
            if self.dump_returncode == 0:
                Path(command[command.index("--file") + 1]).write_bytes(self.dump_bytes)
            return SimpleNamespace(returncode=self.dump_returncode)
        if self.restore_error:
            raise self.restore_error
        return SimpleNamespace(returncode=self.restore_returncode)


class DatabaseBackupServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.staging = Path(self.temp.name) / "database-backup"
        self.now = datetime(2026, 8, 19, 22, 0, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def settings(self, **overrides):
        values = {
            "DATABASE_BACKUP_ENABLED": True,
            "DATABASE_BACKUP_STAGING_DIRECTORY": str(self.staging),
            "DATABASE_BACKUP_MIN_FREE_BYTES": 1024,
            "DATABASE_URL": "postgresql+psycopg://backup_user:secret-value@db.internal:5432/creative_assets",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def service(self, runner=None, *, free=1024 * 1024, **settings):
        return DatabaseBackupService(
            self.settings(**settings),
            runner=runner or FakeRunner(),
            disk_usage=lambda _path: SimpleNamespace(free=free),
            now=lambda _timezone: self.now,
        )

    def test_default_settings_are_disabled_with_the_documented_staging_guard(self):
        settings = Settings()
        self.assertFalse(settings.DATABASE_BACKUP_ENABLED)
        self.assertEqual(settings.DATABASE_BACKUP_DRIVE_FOLDER_ID, "")
        self.assertEqual(settings.DATABASE_BACKUP_RETENTION_DAYS, 21)
        self.assertEqual(settings.DATABASE_BACKUP_MAX_FILES, 6)
        self.assertEqual(settings.DATABASE_BACKUP_MIN_FREE_BYTES, 15 * 1024 * 1024 * 1024)
        self.assertEqual(
            settings.DATABASE_BACKUP_STAGING_DIRECTORY,
            "/var/lib/creative-asset-manager/database-backup",
        )
        with self.assertRaises(ValidationError):
            Settings(DATABASE_BACKUP_MIN_FREE_BYTES=0)
        with self.assertRaises(ValidationError):
            Settings(DATABASE_BACKUP_RETENTION_DAYS=0)
        with self.assertRaises(ValidationError):
            Settings(DATABASE_BACKUP_MAX_FILES=0)
        with self.assertRaises(ValidationError):
            Settings(DATABASE_BACKUP_STAGING_DIRECTORY="relative-backups")

    def test_disabled_backup_fails_before_creating_staging_or_starting_pg_dump(self):
        runner = FakeRunner()
        service = self.service(runner, DATABASE_BACKUP_ENABLED=False)
        with self.assertRaises(DatabaseBackupConfigurationError):
            service.create_verified_dump()
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.staging.exists())

    def test_missing_database_url_fails_before_pg_dump(self):
        runner = FakeRunner()
        with self.assertRaises(DatabaseBackupConfigurationError):
            self.service(runner, DATABASE_URL="").create_verified_dump()
        self.assertEqual(runner.calls, [])

    def test_insufficient_space_fails_before_pg_dump(self):
        runner = FakeRunner()
        service = self.service(runner, free=1023)
        with self.assertRaises(DatabaseBackupPreflightError):
            service.create_verified_dump()
        self.assertEqual(runner.calls, [])
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_success_uses_safe_custom_dump_command_and_returns_checksum(self):
        runner = FakeRunner()
        backup = self.service(runner).create_verified_dump()

        self.assertEqual(backup.path.name, "cam-db-20260819-220001+0700.dump")
        self.assertEqual(backup.size_bytes, len(b"custom-dump"))
        self.assertEqual(backup.sha256, hashlib.sha256(b"custom-dump").hexdigest())
        self.assertTrue(backup.path.exists())
        self.assertEqual((self.staging.stat().st_mode & 0o777), 0o750)

        dump_command, dump_kwargs = runner.calls[0]
        self.assertEqual(dump_command[0], "pg_dump")
        self.assertIn("--format=custom", dump_command)
        self.assertIn("--no-owner", dump_command)
        self.assertIn("--no-privileges", dump_command)
        self.assertNotIn("secret-value", " ".join(dump_command))
        self.assertEqual(dump_kwargs["env"]["PGPASSWORD"], "secret-value")
        self.assertEqual(dump_kwargs["env"]["PGDATABASE"], "creative_assets")
        self.assertEqual(runner.calls[1][0][:2], ["pg_restore", "--list"])

    def test_dump_failure_removes_partial_file_and_never_verifies(self):
        runner = FakeRunner(dump_returncode=1)
        with self.assertRaises(DatabaseBackupDumpError):
            self.service(runner).create_verified_dump()
        self.assertEqual([call[0][0] for call in runner.calls], ["pg_dump"])
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_empty_dump_is_rejected_and_cleaned(self):
        runner = FakeRunner(dump_bytes=b"")
        with self.assertRaises(DatabaseBackupVerificationError):
            self.service(runner).create_verified_dump()
        self.assertEqual([call[0][0] for call in runner.calls], ["pg_dump"])
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_pg_restore_failure_is_rejected_and_cleaned(self):
        runner = FakeRunner(restore_returncode=1)
        with self.assertRaises(DatabaseBackupVerificationError):
            self.service(runner).create_verified_dump()
        self.assertEqual([call[0][0] for call in runner.calls], ["pg_dump", "pg_restore"])
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_missing_pg_dump_is_configuration_error_and_cleans_partial_state(self):
        runner = FakeRunner(dump_error=FileNotFoundError("not found"))
        with self.assertRaises(DatabaseBackupConfigurationError):
            self.service(runner).create_verified_dump()
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_missing_pg_restore_is_configuration_error_and_cleans_dump(self):
        runner = FakeRunner(restore_error=FileNotFoundError("not found"))
        with self.assertRaises(DatabaseBackupConfigurationError):
            self.service(runner).create_verified_dump()
        self.assertEqual(list(self.staging.glob("*.dump")), [])

    def test_verified_dump_is_removed_only_by_explicit_success_cleanup(self):
        backup = self.service().create_verified_dump()
        self.assertTrue(backup.path.exists())
        self.service().cleanup_verified_backup(backup)
        self.assertFalse(backup.path.exists())

    def test_cleanup_refuses_a_path_outside_staging(self):
        outside = Path(self.temp.name) / "outside.dump"
        outside.write_bytes(b"keep")
        backup = SimpleNamespace(path=outside)
        with self.assertRaises(DatabaseBackupConfigurationError):
            self.service().cleanup_verified_backup(backup)
        self.assertTrue(outside.exists())

    def test_lock_prevents_second_concurrent_backup(self):
        first = self.service()
        second = self.service()
        with first.exclusive_lock():
            with self.assertRaises(DatabaseBackupAlreadyRunningError):
                second.create_verified_dump()


if __name__ == "__main__":
    unittest.main()
