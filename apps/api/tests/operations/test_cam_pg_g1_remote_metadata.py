"""Unit safety tests for read-only managed backup receipt comparison."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, RemoteDatabaseBackup
from app.modules.database_backup.verified_receipt import write_verified_receipt
from app.operations import cam_pg_g1_remote_metadata as gate

RECEIPT = {"sha256": "a" * 64, "md5": "b" * 32, "size_bytes": 258}
FOLDER = "backup-folder"


def backup(*, folder=FOLDER, md5="b" * 32, sha="a" * 64, size=258):
    return RemoteDatabaseBackup(
        "never-show-remote-id",
        "cam-db-20261023-220000+0700.dump",
        size,
        datetime(2026, 10, 23, tzinfo=timezone.utc),
        (folder,),
        {"cam_kind": DATABASE_BACKUP_KIND, "cam_sha256": sha},
        md5,
    )


class BackupReceiptComparisonTest(unittest.TestCase):
    def test_correct_remote_metadata_is_strict_but_not_restore_pass(self):
        result = gate.compare_receipt_to_backups(RECEIPT, [backup()], FOLDER)
        self.assertEqual(result["G1_DRIVE_CONTENT_MD5_METADATA_MATCH"], "PASS")
        self.assertEqual(result["G1_STREAM_REDOWNLOAD"], "NOT_RUN")
        self.assertEqual(result["G1_ISOLATED_RESTORE"], "NOT_RUN")
        self.assertEqual(result["G1_RECOVERY_GATE"], "BLOCKED")
        self.assertNotIn("never-show-remote-id", str(result))
        self.assertNotIn("a" * 64, str(result))

    def test_refuses_mismatch_folder_or_duplicate(self):
        for items in (
            [backup(md5="c" * 32)],
            [backup(sha="c" * 64)],
            [backup(size=1)],
            [backup(folder="other-folder")],
            [backup(), backup()],
            [],
        ):
            with self.subTest(items=items), self.assertRaises(gate.RemoteMetadataGateError):
                gate.compare_receipt_to_backups(RECEIPT, items, FOLDER)

    def test_private_current_receipt_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_verified_receipt(
                tmp, sha256=RECEIPT["sha256"], local_md5=RECEIPT["md5"],
                remote_md5=RECEIPT["md5"], size_bytes=RECEIPT["size_bytes"],
            )
            self.assertEqual(gate._receipt(tmp)["sha256"], RECEIPT["sha256"])
            file = Path(tmp) / ".latest-verified-remote-backup.json"
            file.chmod(0o644)
            with self.assertRaises(gate.RemoteMetadataGateError):
                gate._receipt(tmp)
            file.chmod(0o600)
            file.unlink()
            with self.assertRaises(gate.RemoteMetadataGateError):
                gate._receipt(tmp)


if __name__ == "__main__":
    unittest.main()
