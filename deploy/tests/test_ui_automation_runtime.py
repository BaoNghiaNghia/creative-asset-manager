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
        self.assertIn("createVisualAnalysis", text)
        self.assertIn("captureDomElements", text)
        self.assertIn("rankSourceHints", text)
        self.assertIn("visual-analysis.md", text)
        self.assertIn('["repair", "baseline-proposal", "baseline-accept"]', text)
        self.assertIn("ignoredRequestFailures", text)
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
        self.assertIn("ui-repair-check:", text)
        self.assertIn("scripts/cam-ui-repair-check.sh", text)
        self.assertIn("ui-autofix:", text)
        self.assertIn("scripts/cam-ui-autofix.sh", text)
        self.assertIn("ui-smart-tests:", text)
        self.assertIn("scripts/cam-ui-run-smart-tests.sh", text)
        self.assertIn("ui-visual-propose:", text)
        self.assertIn("scripts/cam-ui-baseline-propose.sh", text)
        self.assertIn("ui-visual-accept:", text)
        self.assertIn("scripts/cam-ui-baseline-accept.sh", text)
        self.assertIn("ui-visual-update:", text)
        self.assertNotIn("CAM_UI_VISUAL_UPDATE=1", text)
        self.assertIn("ui:qa:analysis:test", (ROOT / "apps/client/package.json").read_text(encoding="utf-8"))

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
        self.assertIn("Direct baseline update is disabled", staging)
        self.assertIn("CAM_UI_VISUAL_SKIP", staging)
        self.assertIn("CAM_UI_CHANGED_FILES", gate)
        self.assertIn("ui:qa:analysis:test", gate)
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

    def test_visual_analyzer_has_actionable_region_and_source_diagnostics(self) -> None:
        analyzer = (ROOT / "apps/client/scripts/ui-qa-visual-analysis.mjs").read_text(
            encoding="utf-8"
        )
        runner = (ROOT / "apps/client/scripts/ui-qa.mjs").read_text(encoding="utf-8")
        self.assertIn("clusterChangedRegions", analyzer)
        self.assertIn("likelyElements", analyzer)
        self.assertIn("actionTargets", analyzer)
        self.assertIn("sourceHints", analyzer)
        self.assertIn("paintRegionBoxes", analyzer)
        self.assertIn("Do not refresh baselines", analyzer)
        self.assertIn("diagnostics", runner)
        self.assertIn("CAM_UI_CHANGED_FILES", runner)
        self.assertIn("--states", runner)
        self.assertIn("selectExecutionSteps", runner)
        self.assertIn('mode: qaMode', runner)

    def test_targeted_repair_runner_is_bounded_and_skips_full_gate_work(self) -> None:
        repair = (ROOT / "scripts/cam-ui-repair-check.sh").read_text(encoding="utf-8")
        self.assertIn("CAM_UI_REPAIR_MAX_ATTEMPTS", repair)
        self.assertIn("CAM_UI_REPAIR_MAX_TARGETS", repair)
        self.assertIn("--states", repair)
        self.assertIn("--mode repair", repair)
        self.assertIn("npm run dev", repair)
        self.assertIn("git diff --check", repair)
        self.assertIn("repair-sessions", repair)
        self.assertIn("CAM_UI_REPAIR_TYPECHECK", repair)
        self.assertIn("cam-ui-run-smart-tests.sh", repair)
        self.assertIn("CAM_UI_REPAIR_SESSION_ID", repair)
        self.assertNotIn("npm run build", repair)
        self.assertNotIn("deploy-cam-frontend.sh", repair)
        self.assertNotIn("scripts/cam-ui-gate.sh", repair)

    def test_autofix_orchestrator_bounds_repairs_and_final_gate(self) -> None:
        autofix = (ROOT / "scripts/cam-ui-autofix.sh").read_text(encoding="utf-8")
        gate = (ROOT / "scripts/cam-ui-gate.sh").read_text(encoding="utf-8")
        smart = (ROOT / "scripts/cam-ui-run-smart-tests.sh").read_text(encoding="utf-8")
        self.assertIn("CAM_UI_AUTOFIX_MAX_REPAIRS", autofix)
        self.assertIn("must be 1 or 2", autofix)
        self.assertIn("cam-ui-repair-check.sh", autofix)
        self.assertIn("cam-ui-gate.sh", autofix)
        self.assertIn("final-attempted", autofix)
        self.assertIn("will not rerun the full gate automatically", autofix)
        self.assertIn("CAM_UI_AUTOFIX_PLAN_ONLY", autofix)
        self.assertNotIn("deploy-cam-frontend.sh", autofix)
        self.assertIn("cam-ui-run-smart-tests.sh", gate)
        self.assertIn("CAM_UI_SKIP_FRONTEND_TESTS", gate)
        self.assertIn("CAM_UI_SKIP_FRONTEND_TESTS", autofix)
        self.assertIn("TARGETED_VERIFIED", autofix)
        self.assertIn("CAM_UI_SMART_TESTS", smart)
        self.assertIn("ui-smart-tests.mjs", smart)

    def test_smart_test_selector_has_safe_escalation_and_dependency_mapping(self) -> None:
        selector = (ROOT / "apps/client/scripts/ui-smart-tests.mjs").read_text(
            encoding="utf-8"
        )
        planner = (ROOT / "apps/client/scripts/ui-autofix-plan.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn("dependency-linked-tests", selector)
        self.assertIn("broad-or-security-sensitive-frontend-change", selector)
        self.assertIn("frontend-code-change-without-confident-test-map", selector)
        self.assertIn("visual-or-automation-only-change", selector)
        self.assertIn("visualProfileSupported", planner)
        self.assertIn("no-fixture-backed-visual-profile-for", planner)

    def test_baseline_governance_requires_proposal_and_explicit_acceptance(self) -> None:
        runner = (ROOT / "apps/client/scripts/ui-qa.mjs").read_text(encoding="utf-8")
        propose = (ROOT / "scripts/cam-ui-baseline-propose.sh").read_text(encoding="utf-8")
        accept = (ROOT / "scripts/cam-ui-baseline-accept.sh").read_text(encoding="utf-8")
        governance = (ROOT / "scripts/ui_baseline_governance.py").read_text(encoding="utf-8")
        self.assertIn("Direct visual baseline writes are disabled", runner)
        self.assertIn("CAM_UI_TASK", propose)
        self.assertIn("Tracked baselines were not modified", propose)
        self.assertIn("CAM_UI_BASELINE_ACCEPT", accept)
        self.assertIn("CAM_UI_BASELINE_ACCEPT_REASON", accept)
        self.assertIn("baseline-accept-backup", accept)
        self.assertIn("workspaceFingerprint", governance)
        self.assertIn("manifestHash", governance)
        self.assertNotIn("deploy-cam-frontend.sh", propose)
        self.assertNotIn("deploy-cam-frontend.sh", accept)


if __name__ == "__main__":
    unittest.main()
