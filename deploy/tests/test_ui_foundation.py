from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UiFoundationTests(unittest.TestCase):
    def test_foundation_stylesheet_loads_last(self) -> None:
        main = (ROOT / "apps/client/app/main.tsx").read_text(encoding="utf-8")
        foundation_import = 'import "../styles/ui-foundation.css";'
        self.assertIn(foundation_import, main)
        self.assertTrue(
            main.rfind(foundation_import) > main.rfind('import "../styles/responsive-platform.css";')
        )

    def test_foundation_defines_shared_tokens(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        for token in (
            "--cam-font-2xs",
            "--cam-font-base",
            "--cam-space-4",
            "--cam-radius-md",
            "--cam-control-touch",
            "--cam-color-focus",
            "--cam-z-dropdown",
            "--cam-z-modal",
        ):
            self.assertIn(token, text)

    def test_focus_and_reduced_motion_contracts_exist(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(":focus-visible", text)
        self.assertIn("outline: 2px solid var(--cam-color-focus)", text)
        self.assertIn("@media (prefers-reduced-motion: reduce)", text)
        self.assertIn("transition-duration: 0.01ms !important", text)

    def test_dense_workspaces_have_readability_floor(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".review-board-row-meta", text)
        self.assertIn(".rrugc-metrics span", text)
        self.assertIn(".inventory-pipeline-stage small", text)
        self.assertIn("font-size: var(--cam-font-2xs)", text)
        self.assertIn("min-height: var(--cam-control-sm)", text)
        self.assertIn("min-height: var(--cam-control-touch)", text)

    def test_foundation_does_not_change_asset_explorer_input_focus_baseline(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        focus_block = text.split("/* Keep common overlays", 1)[0]
        self.assertNotIn("input", focus_block.split("Shared keyboard focus", 1)[1])


if __name__ == "__main__":
    unittest.main()
