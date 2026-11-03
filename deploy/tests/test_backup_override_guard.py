"""Ensure disk-pruning only trusts an exact current-only backup override."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import unittest

HELPER = Path(__file__).resolve().parents[2] / "scripts/cam-backup-override-guard.sh"
EXACT = (
    "[Service]\n"
    "WorkingDirectory=/opt/creative-asset-manager/current/apps/api\n"
    "ExecStart=\n"
    "ExecStart=/opt/creative-asset-manager/current/apps/api/.venv/bin/python"
    " -m app.operations.database_backup_cli backup\n"
)


def allows(path: Path) -> bool:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; cam_backup_override_uses_current_only "$2"',
         "cam-override-guard-test", str(HELPER), str(path)],
        check=False, capture_output=True, text=True,
    )
    return result.returncode == 0


class BackupOverrideCleanupGuardTest(unittest.TestCase):
    def test_current_only_root_owned_unit_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "override.conf"
            path.write_text(EXACT)
            path.chmod(0o644)
            if path.stat().st_uid == 0:
                self.assertTrue(allows(path))

    def test_old_pinned_runtime_and_unexpected_directives_are_not_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "override.conf"
            for content in [
                EXACT.replace("/current/", "/releases/" + "a" * 40 + "/"),
                EXACT + "Environment=BAD=1\n",
                EXACT.replace(" backup\n", " verify-config\n"),
                EXACT.replace("ExecStart=\n", ""),
                "# trusted\n" + EXACT,
            ]:
                path.write_text(content)
                path.chmod(0o644)
                self.assertFalse(allows(path))

    def test_symlinks_and_group_writable_units_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "override.conf"
            path.write_text(EXACT)
            path.chmod(0o664)
            self.assertFalse(allows(path))
            path.chmod(0o600)
            symlink = root / "link.conf"
            symlink.symlink_to(path)
            self.assertFalse(allows(symlink))


if __name__ == "__main__":
    unittest.main()
