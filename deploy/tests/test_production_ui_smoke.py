from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ProductionUiSmokeTests(unittest.TestCase):
    def test_plan_covers_public_and_private_critical_routes(self) -> None:
        plan = json.loads(
            (ROOT / "docs/operations/production-ui-smoke-plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan["schemaVersion"], 1)
        routes = {route["name"]: route for route in plan["routes"]}
        for name in (
            "privacy",
            "terms",
            "asset-explorer",
            "review-board",
            "realistic-review-ugc",
        ):
            self.assertIn(name, routes)
        self.assertTrue(routes["privacy"]["public"])
        self.assertTrue(routes["terms"]["public"])
        self.assertTrue(routes["asset-explorer"]["requiresAuth"])
        self.assertTrue(routes["review-board"]["requiresAuth"])
        self.assertTrue(routes["realistic-review-ugc"]["requiresAuth"])
        self.assertEqual(
            [viewport["name"] for viewport in plan["viewports"]],
            ["desktop", "tabletPortrait", "mobile"],
        )

    def test_plan_uses_only_read_only_interactions_and_exercises_stage3(self) -> None:
        plan = json.loads(
            (ROOT / "docs/operations/production-ui-smoke-plan.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(plan)
        self.assertNotIn('"fill"', serialized)
        self.assertNotIn('"submit"', serialized)
        self.assertNotIn('"press"', serialized)

        rrugc = next(route for route in plan["routes"] if route["name"] == "realistic-review-ugc")
        stage3 = next(state for state in rrugc["states"] if state["name"] == "stage3-review")
        self.assertEqual(stage3["click"], "#rrugc-tab-stage5")
        self.assertEqual(stage3["waitFor"], ".rrugc-stage3")
        assertion_types = {item["type"] for item in stage3["assertions"]}
        self.assertEqual(
            assertion_types,
            {"visible", "width-ratio", "no-horizontal-overflow"},
        )

    def test_browser_runner_blocks_non_read_methods_and_secrets(self) -> None:
        text = (ROOT / "apps/client/scripts/production-ui-smoke.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn('new Set(["GET", "HEAD", "OPTIONS"])', text)
        self.assertIn('context.route("**/*"', text)
        self.assertIn('route.abort("blockedbyclient")', text)
        self.assertIn("blocked-mutation", text)
        self.assertIn("Production UI smoke requires an HTTPS base URL", text)
        self.assertIn("Production UI smoke refuses loopback/local URLs", text)
        self.assertIn("storage state must live outside the repository", text)
        self.assertIn("build-info.json", text)
        self.assertIn('import { firefox } from "playwright";', text)
        self.assertIn("firefox.launch", text)
        self.assertIn("state.click", text)
        self.assertIn('type === "width-ratio"', text)
        self.assertIn('type === "no-horizontal-overflow"', text)
        self.assertIn("coverage:", text)
        self.assertNotIn("--no-sandbox", text)
        self.assertNotIn("chromiumSandbox", text)
        self.assertNotIn("deploy-cam-frontend.sh", text)
        self.assertNotIn("cam-rebuild-backend.sh", text)

    def test_operator_wrapper_protects_storage_state_and_provenance(self) -> None:
        text = (ROOT / "scripts/cam-production-ui-smoke.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CAM_PRODUCTION_UI_STORAGE_STATE", text)
        self.assertIn("expected mode 600 or stricter", text)
        self.assertIn("must live outside the repository", text)
        self.assertIn("merge-base --is-ancestor", text)
        self.assertIn("CAM_PRODUCTION_UI_PUBLIC_ONLY", text)
        self.assertIn("CAM_PRODUCTION_UI_MODE", text)
        self.assertIn("auto|strict|public", text)
        self.assertIn("EFFECTIVE_MODE", text)
        self.assertIn("auto mode is running public-only coverage", text)
        self.assertIn("GET/HEAD/OPTIONS only", text)
        self.assertIn("CAM_PRODUCTION_UI_KEEP_RUNS", text)
        self.assertIn("shutil.rmtree", text)
        self.assertNotIn("deploy-cam-frontend.sh", text)
        self.assertNotIn("cam-rebuild-backend.sh", text)

    def test_deploy_hook_is_explicit_opt_in_only(self) -> None:
        deploy = (ROOT / "scripts/deploy-cam-frontend.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CAM_PRODUCTION_UI_SMOKE_AFTER_DEPLOY", deploy)
        self.assertIn('== "1"', deploy)
        self.assertIn('CAM_PRODUCTION_UI_MODE="${CAM_PRODUCTION_UI_MODE:-auto}"', deploy)
        self.assertIn("cam-production-ui-smoke.sh", deploy)

    def test_makefile_and_package_expose_smoke_without_deploy(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        package = json.loads(
            (ROOT / "apps/client/package.json").read_text(encoding="utf-8")
        )
        self.assertIn("production-ui-smoke:", makefile)
        self.assertIn("scripts/cam-production-ui-smoke.sh", makefile)
        self.assertEqual(
            package["scripts"]["ui:production-smoke"],
            "node scripts/production-ui-smoke.mjs",
        )

    def test_docs_keep_deploy_authorization_separate(self) -> None:
        workflow = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        vps = (ROOT / "docs/operations/VPS_DEPLOYMENT.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Production UI Smoke", workflow)
        self.assertIn("never grants deployment authorization", workflow)
        self.assertIn("GET, HEAD, and OPTIONS", workflow)
        self.assertIn("Read-only Production UI smoke", vps)
        self.assertIn("does not roll back or mutate Production automatically", vps)


if __name__ == "__main__":
    unittest.main()
