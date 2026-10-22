"""No-disk-write pressure alert contract tests."""
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).with_name("cam-disk-watch.py")))


class DiskWatchTest(unittest.TestCase):
    def test_thresholds_cover_80_85_90_95(self):
        classify = MODULE["classify"]
        for value, expected in (
            (79.99, "normal"), (80, "notice"), (84.99, "notice"),
            (85, "warning"), (89.99, "warning"),
            (90, "critical"), (94.99, "critical"), (95, "emergency"),
        ):
            with self.subTest(value=value):
                self.assertEqual(classify(value), expected)

    def test_statvfs_accounts_for_reserved_filesystem_blocks(self):
        class FakeStats:
            f_frsize = 4096
            f_blocks = 1000
            f_bfree = 200
            f_bavail = 150
        with patch("os.statvfs", return_value=FakeStats()):
            report = MODULE["measure"]("/")
        self.assertEqual(report["used_bytes"], 800 * 4096)
        self.assertEqual(report["available_bytes"], 150 * 4096)
        self.assertAlmostEqual(report["used_percent"], 84.21, places=2)
        self.assertEqual(report["level"], "notice")


if __name__ == "__main__":
    unittest.main()
