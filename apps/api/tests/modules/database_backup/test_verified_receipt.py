"""Backup receipt guarantees: private, bounded, and strictly non-forgeable by pathname."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.database_backup.verified_receipt import (
    RECEIPT_NAME, has_recent_verified_receipt, write_verified_receipt,
)

NOW = datetime(2026, 10, 23, 3, tzinfo=timezone.utc)


class VerifiedRemoteBackupReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.args = dict(
            staging_directory=str(self.directory), sha256="a" * 64,
            local_md5="b" * 32, remote_md5="b" * 32,
            size_bytes=1024, completed_at=NOW,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_is_private_and_expires_after_maximum_age(self):
        write_verified_receipt(**self.args)
        receipt = self.directory / RECEIPT_NAME
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertTrue(has_recent_verified_receipt(str(self.directory), now=NOW))
        self.assertTrue(has_recent_verified_receipt(
            str(self.directory), now=NOW + timedelta(hours=107),
        ))
        self.assertFalse(has_recent_verified_receipt(
            str(self.directory), now=NOW + timedelta(hours=109),
        ))
        self.assertFalse(has_recent_verified_receipt(
            str(self.directory), now=NOW - timedelta(seconds=1),
        ))
        self.assertNotIn("secret", receipt.read_text())

    def test_unverified_checksum_never_produces_a_receipt(self):
        bad = {**self.args, "remote_md5": "c" * 32}
        with self.assertRaises(ValueError):
            write_verified_receipt(**bad)
        self.assertFalse((self.directory / RECEIPT_NAME).exists())

    def test_missing_loose_or_malformed_receipt_fails_closed(self):
        self.assertFalse(has_recent_verified_receipt(str(self.directory), now=NOW))
        write_verified_receipt(**self.args)
        path = self.directory / RECEIPT_NAME
        path.chmod(0o644)
        self.assertFalse(has_recent_verified_receipt(str(self.directory), now=NOW))
        path.chmod(0o600)
        path.write_text("{invalid JSON", encoding="utf-8")
        self.assertFalse(has_recent_verified_receipt(str(self.directory), now=NOW))
        path.write_text("x" * 2000, encoding="utf-8")
        self.assertFalse(has_recent_verified_receipt(str(self.directory), now=NOW))

    def test_symlink_target_is_never_opened_or_overwritten(self):
        outside = self.directory / "protected"
        outside.write_text("private-data", encoding="utf-8")
        link = self.directory / RECEIPT_NAME
        link.symlink_to(outside)
        self.assertFalse(has_recent_verified_receipt(str(self.directory), now=NOW))
        with self.assertRaises(ValueError):
            write_verified_receipt(**self.args)
        self.assertEqual(outside.read_text(encoding="utf-8"), "private-data")

    def test_receipt_is_atomic_and_previous_remains_after_invalid_write(self):
        write_verified_receipt(**self.args)
        old_bytes = (self.directory / RECEIPT_NAME).read_bytes()
        with self.assertRaises(ValueError):
            write_verified_receipt(**{**self.args, "sha256": "not-a-hash"})
        self.assertEqual((self.directory / RECEIPT_NAME).read_bytes(), old_bytes)
        self.assertEqual(len(list(self.directory.glob(".backup-receipt.*"))), 0)


if __name__ == "__main__":
    unittest.main()
