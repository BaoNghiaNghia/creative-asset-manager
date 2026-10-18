from __future__ import annotations

import errno
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("cam-vps-release-inventory.py")
SPEC = importlib.util.spec_from_file_location("cam_release_inventory", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class ReleaseInventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "app"
        self.releases = self.root / "releases"
        self.releases.mkdir(parents=True)
        self.current = "a" * 40
        self.previous = "b" * 40
        self.extra = "c" * 40
        for name in (self.current, self.previous, self.extra):
            (self.releases / name).mkdir()
            (self.releases / name / "sentinel").write_text(name)
        (self.root / "current").symlink_to(self.releases / self.current)
        (self.root / "previous").symlink_to(self.releases / self.previous)
        self.proc = self.dir / "proc"
        self.proc.mkdir()

    def inspect(self):
        return mod.inventory(self.root, proc_root=self.proc)

    def test_current_and_previous_are_protected(self):
        report = self.inspect()
        releases = {x["release"]: x for x in report["releases"]}
        self.assertEqual(report["release_count"], 3)
        self.assertEqual(report["protected_count"], 2)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["unknown_count"], 0)
        self.assertEqual(report["PROCESS_SCAN"], "COMPLETE")
        self.assertEqual(releases[self.current]["protected_by"], ["current"])
        self.assertEqual(releases[self.previous]["protected_by"], ["previous"])
        self.assertEqual(releases[self.extra]["classification"], "REVIEW_ONLY")
        self.assertTrue(report["DELETION_PERFORMED"] is False)
        self.assertTrue((self.releases / self.extra / "sentinel").exists())

    def test_process_cwd_protects_other_release(self):
        pid = self.proc / "1234"
        pid.mkdir()
        (pid / "cwd").symlink_to(self.releases / self.extra)
        report = self.inspect()
        entry = next(x for x in report["releases"] if x["release"] == self.extra)
        self.assertIn("pid_1234_cwd", entry["protected_by"])
        self.assertEqual(report["review_count"], 0)

    def test_process_executable_protects_other_release(self):
        executable = self.releases / self.extra / "bin"
        executable.mkdir()
        (executable / "python").write_text("")
        pid = self.proc / "1988"
        pid.mkdir()
        (pid / "exe").symlink_to(executable / "python")
        report = self.inspect()
        entry = next(x for x in report["releases"] if x["release"] == self.extra)
        self.assertIn("pid_1988_exe", entry["protected_by"])

    def test_symlink_away_from_release_root_fails_closed(self):
        (self.root / "current").unlink()
        (self.root / "current").symlink_to(self.dir)
        with self.assertRaisesRegex(mod.InventoryError, "canonical"):
            self.inspect()

    def test_missing_current_fails_closed(self):
        (self.root / "current").unlink()
        with self.assertRaisesRegex(mod.InventoryError, "No valid current"):
            self.inspect()

    def test_dangling_previous_fails_closed(self):
        (self.root / "previous").unlink()
        (self.root / "previous").symlink_to(self.dir / "missing")
        with self.assertRaises(mod.InventoryError):
            self.inspect()

    def test_symlink_release_entry_not_considered_cleanup_candidate(self):
        (self.releases / ("d" * 40)).symlink_to(self.dir)
        report = self.inspect()
        self.assertEqual(report["release_count"], 3)
        self.assertIn("unexpected_release_entry_present", report["warnings"])

    def test_unknown_directory_not_considered_candidate(self):
        (self.releases / "database-backups").mkdir()
        report = self.inspect()
        self.assertEqual(report["release_count"], 3)
        self.assertIn("unexpected_release_entry_present", report["warnings"])

    def test_unreadable_live_process_never_mislabeled_review_only(self):
        process = self.proc / "2468"
        process.mkdir()
        (process / "cwd").symlink_to(self.releases / self.extra)
        original = os.readlink

        def denied_for_process(path):
            if Path(path) == process / "cwd":
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return original(path)

        with patch.object(mod.os, "readlink", side_effect=denied_for_process):
            report = self.inspect()
        entry = next(x for x in report["releases"] if x["release"] == self.extra)
        self.assertEqual(report["PROCESS_SCAN"], "INCOMPLETE")
        self.assertIn("process_references_incomplete", report["warnings"])
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["unknown_count"], 1)
        self.assertEqual(entry["classification"], "UNKNOWN")

    def test_unavailable_directory_size_never_mislabeled_review_only(self):
        with patch.object(mod, "_directory_mib", return_value=None):
            report = self.inspect()
        self.assertIn("release_size_unavailable", report["warnings"])
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["unknown_count"], 1)
        self.assertEqual(report["PROCESS_SCAN"], "COMPLETE")
        entry = next(x for x in report["releases"] if x["release"] == self.extra)
        self.assertEqual(entry["classification"], "UNKNOWN")

    def test_no_process_table_fails_closed(self):
        with self.assertRaisesRegex(mod.InventoryError, "Process table unavailable"):
            mod.inventory(self.root, proc_root=self.dir / "not-a-proc")

    def test_missing_previous_is_only_a_warning(self):
        (self.root / "previous").unlink()
        report = self.inspect()
        self.assertIn("previous_symlink_missing", report["warnings"])
        self.assertEqual(report["ROLLBACK_POINTERS"], "INCOMPLETE")
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["unknown_count"], 2)
        self.assertIn("current", next(x for x in report["releases"]
                                       if x["release"] == self.current)["protected_by"])


if __name__ == "__main__":
    unittest.main()
