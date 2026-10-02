from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UiAutomationWorkflowTests(unittest.TestCase):
    def test_agents_routes_ui_requests_to_project_workflow(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Automatic UI work", text)
        self.assertIn("docs/operations/UI_AUTOMATION_WORKFLOW.md", text)
        self.assertIn("project Playwright runner", text)
        self.assertIn("make ui-autofix", text)
        self.assertIn("Smart Test Selection", text)
        self.assertIn("one final full UI gate", text)
        self.assertIn("Deploy Production only when the current user explicitly asks", text)

    def test_workflow_covers_interaction_and_responsive_qa(self) -> None:
        text = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "default, hover, selected, selected+hover, focus",
            "console errors",
            "failed requests",
            "1440 x 900",
            "390 x 844",
            "Close the Browser session",
            "current user explicitly asks for a",
        ):
            self.assertIn(required, text)

    def test_workflow_is_not_cursor_dependent(self) -> None:
        text = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("independent of paid Cursor features", text)
        self.assertIn("CodeLocal + Browser (Playwright)", text)

    def test_workflow_uses_targeted_repairs_before_the_full_gate(self) -> None:
        text = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Adaptive repair loop", text)
        self.assertIn("UI Auto-Fix Orchestrator", text)
        self.assertIn("Smart Test Selection", text)
        self.assertIn("make ui-repair-check", text)
        self.assertIn("CAM_UI_TASK=", text)
        self.assertIn("two attempts", text)
        self.assertIn("exactly one final full UI gate", text)

    def test_workflow_requires_governed_baseline_acceptance(self) -> None:
        text = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Baseline governance", text)
        self.assertIn("make ui-visual-propose", text)
        self.assertIn("make ui-visual-accept", text)
        self.assertIn("does NOT mutate baselines", text)
        self.assertIn("current user explicitly confirms", text)
        self.assertIn("Direct `--update-baselines`", text)


if __name__ == "__main__":
    unittest.main()
