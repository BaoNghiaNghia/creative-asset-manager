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
        self.assertIn("chromiumSandbox: false", text)
        self.assertIn("--no-sandbox", text)
        self.assertIn("CAM_UI_QA_ALLOWED_HOSTS", text)
        self.assertIn("desktop", text)
        self.assertIn("tabletPortrait", text)
        self.assertIn("mobile", text)
        self.assertIn("requestFailures", text)
        self.assertIn("consoleErrors", text)

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


if __name__ == "__main__":
    unittest.main()
