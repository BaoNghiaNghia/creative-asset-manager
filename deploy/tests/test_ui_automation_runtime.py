from pathlib import Path
import json
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UiAutomationRuntimeTests(unittest.TestCase):
    def test_ui_gate_never_deploys_production(self) -> None:
        text = (ROOT / "scripts/cam-ui-gate.sh").read_text(encoding="utf-8")
        self.assertIn("npm run typecheck", text)
        self.assertIn("npm run build", text)
        self.assertIn("npm run ui:qa", text)
        self.assertNotIn("deploy-cam-frontend.sh", text)
        self.assertNotIn("cam-rebuild-backend.sh", text)

    def test_browser_runner_has_vps_safe_defaults(self) -> None:
        text = (ROOT / "apps/client/scripts/ui-qa.mjs").read_text(encoding="utf-8")
        fixture = (ROOT / "apps/client/scripts/ui-qa-fixture.mjs").read_text(encoding="utf-8")
        self.assertIn("chromiumSandbox: false", text)
        self.assertIn("--no-sandbox", text)
        self.assertIn("CAM_UI_QA_ALLOWED_HOSTS", text)
        self.assertIn("--fixture", text)
        self.assertIn("installUiQaFixture", text)
        self.assertIn("--baseline-dir", text)
        self.assertIn("--update-baselines", text)
        self.assertIn("pixelmatch", text)
        self.assertIn("PNG.sync.read", text)
        self.assertIn("desktop", text)
        self.assertIn("tabletPortrait", text)
        self.assertIn("mobile", text)
        self.assertIn("requestFailures", text)
        self.assertIn("consoleErrors", text)
        self.assertIn('context.route("**/api/**"', fixture)
        self.assertIn("Unhandled UI QA fixture route", fixture)

    def test_example_plan_is_valid_json(self) -> None:
        plan = json.loads(
            (ROOT / "docs/operations/ui-qa-plan.example.json").read_text(encoding="utf-8")
        )
        self.assertTrue(plan["steps"])
        self.assertIn("desktop", plan["viewports"])
        self.assertEqual(plan["steps"][0]["name"], "default")

    def test_makefile_exposes_ui_check(self) -> None:
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("ui-check:", text)
        self.assertIn("scripts/cam-ui-gate.sh", text)
        self.assertIn("ui-staging-qa:", text)
        self.assertIn("scripts/cam-ui-staging-qa.sh", text)
        self.assertIn("ui-visual-update:", text)
        self.assertIn("CAM_UI_VISUAL_UPDATE=1", text)

    def test_authenticated_staging_fixture_is_safe_and_complete(self) -> None:
        fixture = json.loads(
            (ROOT / "apps/client/scripts/fixtures/explorer-viewer.json").read_text(encoding="utf-8")
        )
        plan = json.loads(
            (ROOT / "docs/operations/ui-qa-explorer-viewer-plan.json").read_text(encoding="utf-8")
        )
        gate = (ROOT / "scripts/cam-ui-gate.sh").read_text(encoding="utf-8")
        staging = (ROOT / "scripts/cam-ui-staging-qa.sh").read_text(encoding="utf-8")
        self.assertEqual(fixture["identity"]["roles"], ["viewer"])
        self.assertTrue(fixture["providerSession"]["authenticated"])
        self.assertTrue(fixture["viewerBootstrap"]["auto_selected_source_id"])
        self.assertGreaterEqual(len(fixture["folders"]["qa-root"]["children"]), 4)
        self.assertTrue(any("fill" in step for step in plan["steps"]))
        self.assertIn("Authenticated local staging Browser QA", gate)
        self.assertIn("CAM_UI_QA_SKIP", gate)
        self.assertIn("127.0.0.1", staging)
        self.assertIn("--fixture", staging)
        self.assertIn("--baseline-dir", staging)
        self.assertIn("--update-baselines", staging)
        self.assertIn("CAM_UI_VISUAL_SKIP", staging)
        self.assertNotIn("deploy-cam-frontend.sh", staging)

    def test_visual_baseline_manifest_matches_default_plan(self) -> None:
        baseline_dir = ROOT / "apps/client/visual-baselines/explorer-viewer"
        manifest = json.loads((baseline_dir / "manifest.json").read_text(encoding="utf-8"))
        plan = json.loads(
            (ROOT / "docs/operations/ui-qa-explorer-viewer-plan.json").read_text(encoding="utf-8")
        )
        expected_states = [step["name"] for step in plan["steps"]]
        expected_viewports = plan["viewports"]
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["states"], expected_states)
        self.assertEqual([item["name"] for item in manifest["viewports"]], expected_viewports)
        pngs = sorted(path.name for path in baseline_dir.glob("*.png"))
        self.assertEqual(len(pngs), len(expected_states) * len(expected_viewports))


if __name__ == "__main__":
    unittest.main()
