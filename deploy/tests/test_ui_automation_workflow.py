from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UiAutomationWorkflowTests(unittest.TestCase):
    def test_agents_routes_ui_requests_to_project_workflow(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Automatic UI work", text)
        self.assertIn("docs/operations/UI_AUTOMATION_WORKFLOW.md", text)
        self.assertIn("CodeLocal Browser (Playwright)", text)
        self.assertIn("Deploy Production only when the current user explicitly asks", text)

    def test_workflow_covers_interaction_and_responsive_qa(self) -> None:
        text = (ROOT / "docs/operations/UI_AUTOMATION_WORKFLOW.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "default, hover, selected, selected+hover, focus",
            "console errors",
            "failed network requests",
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


if __name__ == "__main__":
    unittest.main()
