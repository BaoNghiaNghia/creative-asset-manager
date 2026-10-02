from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CursorStagingWorkflowTests(unittest.TestCase):
    def test_cursor_rule_keeps_production_behind_codelocal(self) -> None:
        text = (ROOT / ".cursor/rules/cam-safe-delivery.mdc").read_text(encoding="utf-8")
        self.assertIn("CodeLocal/operator", text)
        self.assertIn("must NOT", text)
        self.assertIn("deploy-cam-frontend.sh", text)
        self.assertIn("cam-rebuild-backend.sh", text)

    def test_worker_is_unprivileged_and_points_to_isolated_checkout(self) -> None:
        text = (
            ROOT / "deploy/systemd/cam-cursor-my-machine.service.example"
        ).read_text(encoding="utf-8")
        self.assertIn("User=cursoragent", text)
        self.assertNotIn("User=root", text)
        self.assertIn("/srv/creative-asset-manager-cursor", text)
        self.assertIn("NoNewPrivileges=true", text)

    def test_bootstrap_does_not_start_worker_before_auth(self) -> None:
        text = (ROOT / "scripts/cam-bootstrap-cursor-my-machine.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("systemctl enable --now", text.split("cat <<EOF", 1)[0])
        self.assertIn("agent login", text)
        self.assertIn("worker debug", text)

    def test_staging_gate_rejects_main_by_default(self) -> None:
        text = (ROOT / "scripts/cam-cursor-staging-gate.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('BRANCH="$(git branch --show-current)"', text)
        self.assertIn('CAM_STAGING_ALLOW_MAIN', text)
        self.assertIn('must run on a task branch, not main', text)

    def test_ui_design_rule_requires_browser_state_verification(self) -> None:
        text = (ROOT / ".cursor/rules/cam-ui-design.mdc").read_text(encoding="utf-8")
        self.assertIn("selected + hover", text)
        self.assertIn("Cursor Browser", text)
        self.assertIn("console errors", text)
        self.assertIn("failed network requests", text)
        self.assertIn("does not authorize production deployment", text)

    def test_ui_design_runbook_keeps_production_as_separate_handoff(self) -> None:
        text = (ROOT / "docs/operations/CURSOR_UI_DESIGN.md").read_text(encoding="utf-8")
        self.assertIn("CodeLocal review", text)
        self.assertIn("do not deploy production", text)
        self.assertIn("1440 x 900", text)
        self.assertIn("selected + hover", text)


if __name__ == "__main__":
    unittest.main()
