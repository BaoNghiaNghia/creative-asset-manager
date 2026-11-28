from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from frontend_release_guard import validate_release, validate_assets


class FrontendReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate = self.root / "candidate"
        self.active = self.root / "active"
        self.candidate.mkdir()
        self.active.mkdir()
        assets = self.candidate / "assets"
        assets.mkdir()
        (assets / "inter-latin-wght-normal-abc.woff2").write_bytes(b"font")
        (assets / "be-vietnam-pro-latin-400-normal-abc.woff2").write_bytes(b"font")
        (assets / "index-a.css").write_text("--cam-icon-blue:;--cam-icon-mint:;--cam-icon-orange:;--cam-nav-font:;--etsy-demand-column:;.etsy-demand-metrics{}")
        self.info(self.candidate, "b" * 40, 5)
        self.info(self.active, "a" * 40, 0)

    @staticmethod
    def info(folder, commit, revision):
        (folder / "build-info.json").write_text(json.dumps({
            "build_commit": commit, "ui_contract": "cam-color-mix-v5",
            "ui_revision": revision,
        }))

    def test_descendant_with_complete_assets_passes(self):
        with patch("frontend_release_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            validate_release(self.candidate, self.active, self.root, "b" * 40)
        self.assertIn("a" * 40, run.call_args.args[0])

    def test_divergent_branch_is_rejected(self):
        with patch("frontend_release_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, "merge first"):
                validate_release(self.candidate, self.active, self.root, "b" * 40)

    def test_unknown_live_build_is_rejected(self):
        with patch("frontend_release_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 128)):
            with self.assertRaises(ValueError):
                validate_release(self.candidate, self.active, self.root, "b" * 40)

    def test_missing_v5_contract_is_rejected(self):
        self.info(self.candidate, "b" * 40, 0)
        with self.assertRaisesRegex(ValueError, "contract"):
            validate_assets(self.candidate)

    def test_missing_font_is_rejected(self):
        (self.candidate / "assets/inter-latin-wght-normal-abc.woff2").unlink()
        with self.assertRaisesRegex(ValueError, "Inter"):
            validate_assets(self.candidate)

    def test_newer_live_ui_is_protected(self):
        self.info(self.active, "a" * 40, 6)
        with patch("frontend_release_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(ValueError, "downgrade"):
                validate_release(self.candidate, self.active, self.root, "b" * 40)


if __name__ == "__main__":
    unittest.main()
