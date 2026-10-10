"""Regression tests for safe post-deploy Browser QA retention."""
import importlib.util
import json
from pathlib import Path
import os
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cam-clean-browser-qa.py"
spec = importlib.util.spec_from_file_location("cam_clean_browser_qa", SCRIPT)
clean = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(clean)


class BrowserQaCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = Path(self.temp.name) / "apps/client"
        self.root = self.client / ".ui-qa"
        self.root.mkdir(parents=True)
        self.now = 1_800_000_000

    def tearDown(self):
        self.temp.cleanup()

    def run_folder(self, parent, name, days_ago=10, report="pass"):
        folder = parent / name
        folder.mkdir(parents=True)
        if report:
            (folder / "report.json").write_text(json.dumps({"summary": {"status": report}}))
        (folder / "evidence.png").write_bytes(b"test-data" * 100)
        os.utime(folder, (self.now - days_ago * clean.DAY, self.now - days_ago * clean.DAY))
        return folder

    def clean(self, dry_run=False):
        return clean.cleanup_browser_qa(self.root, client=self.client, now=self.now, dry_run=dry_run)

    def test_successful_qa_runs_retained_recent_and_failed_are_protected(self):
        names = [
            "2026-10-01T00-00-00-000Z",
            "2026-10-02T00-00-00-000Z",
            "2026-10-03T00-00-00-000Z",
            "2026-10-04T00-00-00-000Z",
            "2026-10-05T00-00-00-000Z",
            "2026-10-06T00-00-00-000Z",
        ]
        for index, name in enumerate(names):
            self.run_folder(self.root, name, days_ago=10-index,
                            report="fail" if index == 1 else "pass")
        (self.root / "autofix-sessions").mkdir()
        (self.root / "autofix-sessions/critical.log").write_text("do not delete")
        result = self.clean()
        self.assertGreater(result["bytes_reclaimed"], 0)
        self.assertEqual(result["removed"], 2)
        self.assertGreater(result["protected_failures"], 0)
        self.assertTrue((self.root / names[1]).exists())
        self.assertFalse((self.root / names[0]).exists())
        self.assertTrue((self.root / names[-1]).exists())
        self.assertTrue((self.root / "autofix-sessions/critical.log").exists())

    def test_production_smoke_keeps_five_and_protects_recent_and_failed(self):
        base = self.root / "production-smoke"
        iso = [f"2026-10-{day:02d}T00-00-00-000Z" for day in range(1, 8)]
        for i, name in enumerate(iso):
            self.run_folder(base, name, days_ago=9-i, report="fail" if i == 0 else "pass")
        compact = self.run_folder(base, "20261010T050601Z", days_ago=12)
        result = self.clean()
        self.assertGreaterEqual(result["removed"], 1)
        self.assertTrue((base / iso[0]).exists())
        self.assertFalse(compact.exists())

    def test_unaccepted_baselines_are_never_deleted_and_accepted_have_copies(self):
        proposals = self.root / "baseline-proposals"
        proposal_names = [
            "20261001T010101Z-abc-preview",
            "20261002T010101Z-def-preview",
            "20261003T010101Z-ghi-preview",
        ]
        for i, name in enumerate(proposal_names):
            folder = self.run_folder(proposals, name, days_ago=10-i, report="fail")
            (folder / "report.json").write_text(json.dumps(
                {"visual": {"baselineDir": "visual-baselines/review-board"}}))
            (folder / "acceptance.json").write_text(json.dumps(
                {"changedScreenshots": ["desktop--default.png"]}))
        baseline = self.client / "visual-baselines/review-board/desktop--default.png"
        baseline.parent.mkdir(parents=True)
        baseline.write_bytes(b"permanent baseline")
        # Delete accepted evidence only after verifying retained image copies.
        res = self.clean()
        self.assertEqual(res["removed"], 1)
        self.assertFalse((proposals / proposal_names[0]).exists())
        self.assertTrue(baseline.is_file())
        # Without a verified copy, it must not be deleted.
        baseline.unlink()
        fourth = self.run_folder(proposals, "20260930T010101Z-jkl-preview", days_ago=20)
        (fourth / "report.json").write_text(json.dumps(
            {"visual": {"baselineDir": "visual-baselines/review-board"}}))
        (fourth / "acceptance.json").write_text(json.dumps(
            {"changedScreenshots": ["desktop--default.png"]}))
        self.clean()
        self.assertTrue(fourth.exists())

    def test_dry_run_does_not_delete_and_symlinks_are_ignored(self):
        base = self.root / "production-smoke"
        for i in range(7):
            self.run_folder(base, f"2026-10-{i+1:02d}T00-00-00-000Z",
                            days_ago=12-i)
        external = Path(self.temp.name) / "product-images"
        external.mkdir()
        (external / "precious.png").write_bytes(b"keep")
        (base / "2026-09-01T00-00-00-000Z").symlink_to(external, target_is_directory=True)
        result = self.clean(dry_run=True)
        self.assertGreater(result["removed"], 0)
        self.assertEqual(len(list(base.iterdir())), 8)
        self.clean()
        self.assertTrue((external / "precious.png").exists())
        self.assertTrue((base / "2026-09-01T00-00-00-000Z").is_symlink())

    def test_unknown_directories_without_reports_survive_first_week(self):
        root_dir = self.run_folder(self.root, "2026-10-08T12-00-00-000Z",
                                   days_ago=4, report=None)
        for i in range(3):
            self.run_folder(self.root, f"2026-10-0{i+1}T00-00-00-000Z",
                            days_ago=2, report="pass")
        self.clean()
        self.assertTrue(root_dir.exists())

    def test_deploy_hook_runs_after_health_checks(self):
        source = (SCRIPT.parent / "deploy-cam-frontend.sh").read_text()
        self.assertIn("CAM_BROWSER_QA_CLEANUP_AFTER_DEPLOY", source)
        self.assertIn("cam-clean-browser-qa.py", source)
        self.assertLess(source.index("for path in / /build-info.json"),
                        source.index("cam-clean-browser-qa.py"))


if __name__ == "__main__":
    unittest.main()
