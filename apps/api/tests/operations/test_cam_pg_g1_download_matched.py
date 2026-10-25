"""Safety tests for downloading G1 backup to an independent volume only."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND
from app.operations import cam_pg_g1_download_matched as module

RECEIPT = {"sha256": "a" * 64, "md5": "b" * 32, "size_bytes": 327706668}


def remote(*, md5="b" * 32, sha="a" * 64):
    return SimpleNamespace(
        remote_file_id="never-print-or-expose-this-id",
        parents=("private-backup-folder",),
        app_properties={"cam_kind": DATABASE_BACKUP_KIND, "cam_sha256": sha},
        size_bytes=RECEIPT["size_bytes"],
        md5_checksum=md5,
    )


class DownloadMatchedGateTests(unittest.TestCase):
    def test_unique_metadata_match_without_exposing_id_in_report(self):
        found = module.select_unique_receipt_backup(
            RECEIPT, [remote()], "private-backup-folder"
        )
        self.assertEqual(found, "never-print-or-expose-this-id")

    def test_mismatch_or_duplicate_fail_closed(self):
        for candidates in (
            [], [remote(md5="c" * 32)], [remote(sha="c" * 64)],
            [remote(), remote()],
        ):
            with self.subTest(candidates=len(candidates)), self.assertRaises(Exception):
                module.select_unique_receipt_backup(
                    RECEIPT, candidates, "private-backup-folder"
                )

    def test_same_production_device_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(module.os, "geteuid", return_value=0):
                with self.assertRaises(module.DownloadGateError):
                    module.check_separate_volume(Path(td))

    def test_must_use_root_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(module.os, "geteuid", return_value=1000):
                with self.assertRaises(module.DownloadGateError):
                    module.check_separate_volume(root)
            link = root / "alias"
            link.symlink_to(root, target_is_directory=True)
            with patch.object(module.os, "geteuid", return_value=0):
                with self.assertRaises(module.DownloadGateError):
                    module.check_separate_volume(link)

    def test_separate_volume_capacity_gate(self):
        dest = Path("/mnt/cam-db-g1-private")
        real_stat = Path.stat

        def faked_stat(path, *args, **kwargs):
            if path == dest:
                return SimpleNamespace(st_dev=999, st_mode=0o40700)
            if path == Path("/"):
                return SimpleNamespace(st_dev=1, st_mode=0o40755)
            return real_stat(path, *args, **kwargs)

        with patch.object(module.os, "geteuid", return_value=0), \
             patch.object(module.Path, "stat", autospec=True, side_effect=faked_stat), \
             patch.object(module, "_private_directory", return_value=dest), \
             patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=19 * module.GIB)):
            with self.assertRaises(module.DownloadGateError):
                module.check_separate_volume(dest)

        with patch.object(module.os, "geteuid", return_value=0), \
             patch.object(module.Path, "stat", autospec=True, side_effect=faked_stat), \
             patch.object(module, "_private_directory", return_value=dest), \
             patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=25 * module.GIB)):
            module.check_separate_volume(dest)

    def test_report_contains_no_id_hashes_and_requires_independent_stream(self):
        dest = Path("/mnt/cam-db-g1-private")
        storage = SimpleNamespace(folder_id="private-backup-folder")

        async def list_backups():
            return [remote()]

        async def verified_download(**_kwargs):
            return {
                "sha256": RECEIPT["sha256"], "md5": RECEIPT["md5"],
                "size_bytes": RECEIPT["size_bytes"], "file_path": str(dest / "safe.dump")
            }

        storage.list_managed_backups = list_backups
        settings = SimpleNamespace(DATABASE_BACKUP_STAGING_DIRECTORY="/protected/staging")
        with patch.object(module, "check_separate_volume"), \
             patch.object(module, "get_settings", return_value=settings), \
             patch.object(module, "verify_configuration"), \
             patch.object(module, "_receipt", return_value=RECEIPT), \
             patch.object(module, "build_database_backup_storage", return_value=storage), \
             patch.object(module, "download_managed_backup", side_effect=verified_download):
            report = asyncio.run(module.download_verified_backup(dest))

        self.assertEqual(report["G1_DRIVE_STREAM_VERIFIED"], "PASS")
        self.assertEqual(report["G1_ISOLATED_RESTORE"], "NOT_RUN")
        self.assertEqual(report["G1_RECOVERY_GATE"], "BLOCKED")
        self.assertNotIn("never-print-or-expose-this-id", str(report))
        self.assertNotIn("a" * 64, str(report))


if __name__ == "__main__":
    unittest.main()
