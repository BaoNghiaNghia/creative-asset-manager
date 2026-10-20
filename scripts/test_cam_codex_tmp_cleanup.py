#!/usr/bin/env python3
"""Unit checks for the metadata-only, temp-only Codex janitor."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("cam-codex-tmp-cleanup.py")
spec = importlib.util.spec_from_file_location("cam_codex_tmp_cleanup", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CodexTempCleanupTests(unittest.TestCase):
    def test_dry_run_does_not_remove_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            group = root / "sub"
            group.mkdir()
            old = group / "zero"
            old.touch()
            ts = time.time() - 72 * 3600
            os.utime(old, (ts, ts))
            os.utime(group, (ts, ts))
            report = module.inspect(root, time.time() - 48*3600, False, 100)
            self.assertEqual(report[0:2], (1, 1))
            self.assertTrue(old.exists())
            self.assertTrue(group.exists())

    def test_apply_only_stale_empty_and_never_nonempty_or_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_dir = root / "old"
            old_dir.mkdir()
            old_file = old_dir / "zero"
            old_file.touch()
            live = root / "data"
            live.write_bytes(b"preserve-me")
            link = root / "shortcut"
            link.symlink_to(live)
            ts = time.time() - 72 * 3600
            os.utime(old_file, (ts, ts))
            os.utime(old_dir, (ts, ts))
            with patch.object(module, "codex_is_running", return_value=False):
                report = module.inspect(root, time.time() - 48*3600, True, 100)
            self.assertGreaterEqual(report[3], 2)
            self.assertFalse(old_file.exists())
            self.assertFalse(old_dir.exists())
            self.assertEqual(live.read_bytes(), b"preserve-me")
            self.assertTrue(link.is_symlink())
            self.assertTrue(root.is_dir())

    def test_worker_presence_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "oldzero"
            old.touch()
            ts = time.time() - 72 * 3600
            os.utime(old, (ts, ts))
            with patch.object(module, "codex_is_running", return_value=True):
                report = module.inspect(root, time.time() - 48*3600, True, 100)
            self.assertEqual(report[3], 0)
            self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
