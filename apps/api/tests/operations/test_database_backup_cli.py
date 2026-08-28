from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, RemoteDatabaseBackup
from app.modules.database_backup.retention import DatabaseBackupRetentionResult
from app.modules.database_backup.service import DatabaseBackupConfigurationError, VerifiedDatabaseBackup
from app.modules.database_backup.workflow import DatabaseBackupWorkflowResult
from app.operations import database_backup_cli as cli


def settings(**overrides):
    values = {
        "DATABASE_BACKUP_ENABLED": True,
        "DATABASE_BACKUP_DRIVE_FOLDER_ID": "backup-folder",
        "DATABASE_BACKUP_RETENTION_DAYS": 21,
        "DATABASE_BACKUP_MAX_FILES": 6,
        "DATABASE_BACKUP_MIN_FREE_BYTES": 16106127360,
        "DATABASE_BACKUP_STAGING_DIRECTORY": "/var/lib/creative-asset-manager/database-backup",
        "DATABASE_URL": "postgresql+psycopg://user:secret@db/creative_assets",
        "GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID": "managed-root",
        "GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN": "managed-token",
        "GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN": "",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DatabaseBackupCliTest(unittest.IsolatedAsyncioTestCase):
    def test_verify_config_is_read_only_and_requires_managed_identity(self):
        result = cli.verify_configuration(settings(), which=lambda command: f"/usr/bin/{command}")
        self.assertEqual(result["DATABASE_BACKUP_CONFIGURATION"], "VALID")
        self.assertEqual(result["TENANT_SOURCE_DRIVE_OAUTH"], "NOT_USED")
        with self.assertRaises(DatabaseBackupConfigurationError):
            cli.verify_configuration(
                settings(
                    GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN="",
                    GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN="",
                ),
                which=lambda command: f"/usr/bin/{command}",
            )

    def test_verify_config_fails_closed_when_postgres_tools_are_missing(self):
        with self.assertRaises(DatabaseBackupConfigurationError):
            cli.verify_configuration(settings(), which=lambda _command: None)

    async def test_list_is_read_only_and_returns_only_safe_metadata(self):
        remote = SimpleNamespace(
            list_managed_backups=lambda: _async_result([
                RemoteDatabaseBackup(
                    "remote-1",
                    "cam-db-20260828-220000+0700.dump",
                    123,
                    datetime(2026, 8, 28, tzinfo=timezone.utc),
                    ("backup-folder",),
                    {"cam_kind": DATABASE_BACKUP_KIND},
                )
            ])
        )
        with patch.object(cli, "verify_configuration"), patch.object(
            cli,
            "build_components",
            return_value=(SimpleNamespace(), remote, SimpleNamespace()),
        ):
            result = await cli.list_backups(settings())
        self.assertEqual(result["READ_ONLY"], "YES")
        self.assertEqual(result["REMOTE_BACKUP_COUNT"], 1)
        self.assertNotIn("managed-token", str(result))
        self.assertFalse(hasattr(remote, "delete_managed_backup"))

    async def test_backup_result_reports_verified_retention_and_cleanup(self):
        backup = VerifiedDatabaseBackup(
            Path("/var/lib/creative-asset-manager/database-backup/cam.dump"),
            datetime(2026, 8, 28, tzinfo=timezone.utc),
            123,
            "a" * 64,
            "b" * 32,
        )
        workflow_result = DatabaseBackupWorkflowResult(
            backup,
            RemoteDatabaseBackup(
                "remote-1", "cam.dump", 123, datetime.now(timezone.utc),
                ("backup-folder",), {"cam_kind": DATABASE_BACKUP_KIND},
                "b" * 32,
            ),
            DatabaseBackupRetentionResult(7, 1, 0, 6),
        )
        workflow = SimpleNamespace(run=lambda: _async_result(workflow_result))
        with patch.object(cli, "verify_configuration"), patch.object(
            cli, "build_components", return_value=(object(), object(), object())
        ), patch.object(cli, "DatabaseBackupWorkflow", return_value=workflow):
            result = await cli.run_backup(settings())
        self.assertEqual(result["DATABASE_BACKUP"], "SUCCESS")
        self.assertEqual(result["REMOTE_BACKUP_COUNT"], 6)
        self.assertEqual(result["LOCAL_CLEANUP"], "PASS")
        self.assertEqual(result["BACKUP_MD5"], "b" * 32)
        self.assertEqual(result["REMOTE_MD5_CHECKSUM"], "b" * 32)

    def test_failure_output_never_includes_exception_message(self):
        output = io.StringIO()
        with redirect_stdout(output):
            cli._print_failure(DatabaseBackupConfigurationError("password=secret"))
        self.assertNotIn("secret", output.getvalue())
        self.assertIn("ERROR_TYPE=DatabaseBackupConfigurationError", output.getvalue())

    def test_systemd_schedule_and_service_contract(self):
        root = Path(__file__).resolve().parents[4]
        timer = (root / "deploy/systemd/creative-asset-manager-db-backup.timer").read_text()
        service = (root / "deploy/systemd/creative-asset-manager-db-backup.service").read_text()
        self.assertIn("OnCalendar=Tue,Fri *-*-* 22:00:00 Asia/Ho_Chi_Minh", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("AccuracySec=1min", timer)
        self.assertIn("Type=oneshot", service)
        self.assertIn("User=creative-assets", service)
        self.assertIn("WorkingDirectory=/opt/creative-asset-manager/current/apps/api", service)
        self.assertIn("database_backup_cli backup", service)
        self.assertIn("ReadWritePaths=/var/lib/creative-asset-manager/database-backup", service)


async def _async_result(value):
    return value
