"""Isolated regression checks for frontend immutable rollback retention."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[2] / "scripts" / "deploy-cam-frontend.sh"


class FrontendReleaseRetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "cam-frontend"
        releases = self.root / "releases"
        releases.mkdir(parents=True)
        self.active = releases / ("a" * 40)
        self.previous = releases / ("b" * 40)
        self.extra = releases / ("c" * 40)
        for path in (self.active, self.previous, self.extra):
            path.mkdir()
            (path / "index.html").write_text("sandbox-only")
        (self.root / "current").symlink_to(self.active)
        (self.root / "previous").symlink_to(self.previous)
        source = FRONTEND.read_text(encoding="utf-8")
        prefix = "if $ROLLBACK; then"
        start = source.index(prefix)
        end = source.index("\nfi", start) + 3
        self.rollback = source[start:end]
        self.source = source

    def run_rollback(self, *, fail_activation: bool = False):
        # Only run the rollback branch with sandbox-specific nginx/systemctl
        # functions; never execute privileged production preflight/deploy.
        code = (
            "set -Eeuo pipefail\n"
            "ROLLBACK=true\n"
            f"WEB_ROOT={str(self.root)!r}\n"
            "die() { printf '%s\\n' \"$1\" >&2; exit 9; }\n"
            "check_count=0\n"
            "nginx() { check_count=$((check_count+1)); "
            + ("if [[ $check_count -eq 2 ]]; then return 1; fi; " if fail_activation else "")
            + "return 0; }\n"
            "systemctl() { [[ \"$1\" == reload && \"$2\" == nginx ]]; }\n"
            + self.rollback
            + "\n"
        )
        return subprocess.run(
            ["bash", "-c", code], capture_output=True, text=True, timeout=5,
        )

    def test_frontend_deploy_never_deletes_unreviewed_releases(self):
        source = self.source
        self.assertIn("no automatic deletion", source)
        self.assertNotIn('rm -f -- "$WEB_ROOT/previous"', source)
        self.assertNotIn('rm -rf -- "$release"', source)
        self.assertIn('ln -s -- "$OLD" "$WEB_ROOT/previous.new"', source)
        self.assertIn('mv -Tf -- "$WEB_ROOT/previous.new" "$WEB_ROOT/previous"', source)

    def test_rollback_swaps_current_previous_and_keeps_third_release(self):
        result = self.run_rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "current").resolve(), self.previous)
        self.assertEqual((self.root / "previous").resolve(), self.active)
        self.assertTrue((self.extra / "index.html").exists())
        # A second rollback can safely reverse the first.
        result = self.run_rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "current").resolve(), self.active)
        self.assertEqual((self.root / "previous").resolve(), self.previous)

    def test_failed_reload_restores_active_release_and_previous(self):
        result = self.run_rollback(fail_activation=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "current").resolve(), self.active)
        self.assertEqual((self.root / "previous").resolve(), self.previous)
        self.assertTrue((self.extra / "index.html").exists())

    def test_invalid_rollback_symlink_target_blocked(self):
        (self.root / "previous").unlink()
        (self.root / "previous").symlink_to(self.root.parent)
        result = self.run_rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "current").resolve(), self.active)

    def test_same_current_previous_rejected(self):
        (self.root / "previous").unlink()
        (self.root / "previous").symlink_to(self.active)
        result = self.run_rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "current").resolve(), self.active)


if __name__ == "__main__":
    unittest.main()
