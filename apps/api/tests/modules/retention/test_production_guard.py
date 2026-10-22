"""Safety checks must block production database mutation with no trusted backup."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.database_backup.verified_receipt import write_verified_receipt
from app.modules.retention.production_guard import (
    RetentionProductionGuardError, enforce_retention_guard,
    retention_block_reason,
)
from app.modules.retention.service import RetentionCleanupService

NOW = datetime(2026, 10, 23, 3, tzinfo=timezone.utc)


class RetentionProductionGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.settings = SimpleNamespace(
            is_production=True,
            RETENTION_CLEANUP_MIN_FREE_BYTES=8 * 1024**3,
            RETENTION_CLEANUP_BACKUP_MAX_AGE_HOURS=108,
            DATABASE_BACKUP_ENABLED=True,
            DATABASE_BACKUP_STAGING_DIRECTORY=str(self.directory),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def issue_receipt(self):
        write_verified_receipt(
            str(self.directory), sha256="a" * 64, local_md5="b" * 32,
            remote_md5="b" * 32, size_bytes=123, completed_at=NOW,
        )

    def test_missing_backup_receipt_blocks_even_with_ample_disk(self):
        self.assertEqual(
            retention_block_reason(self.settings, now=NOW, available_bytes=10 * 1024**3),
            "remote_verified_backup_receipt_missing_or_stale",
        )

    def test_low_disk_blocks_even_with_recent_verified_backup(self):
        self.issue_receipt()
        self.assertEqual(
            retention_block_reason(self.settings, now=NOW, available_bytes=7 * 1024**3),
            "insufficient_disk_headroom",
        )
        self.assertIsNone(
            retention_block_reason(self.settings, now=NOW, available_bytes=8 * 1024**3),
        )

    def test_backup_flag_disabled_fails_closed(self):
        self.issue_receipt()
        self.settings.DATABASE_BACKUP_ENABLED = False
        self.assertEqual(
            retention_block_reason(self.settings, now=NOW, available_bytes=12 * 1024**3),
            "backup_feature_disabled",
        )

    def test_development_does_not_require_production_backup(self):
        self.settings.is_production = False
        self.assertIsNone(retention_block_reason(self.settings, now=NOW, available_bytes=0))

    def test_mutating_execute_never_enters_database_when_preflight_fails(self):
        factory = lambda: self.fail("database must not be opened")
        service = RetentionCleanupService(factory, self.settings)
        with patch(
            "app.modules.retention.production_guard.os.statvfs"
        ) as statvfs:
            statvfs.return_value = SimpleNamespace(f_bavail=100, f_frsize=4096)
            with self.assertRaises(RetentionProductionGuardError):
                service.execute(tenant_id="tenant-a", run_id="run-a")


if __name__ == "__main__":
    unittest.main()
