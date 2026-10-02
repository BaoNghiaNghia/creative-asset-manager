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
            "--cam-page-inline",
            "--cam-page-block",
            "--cam-section-gap",
            "--cam-panel-gap",
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

    def test_responsive_foundation_uses_canonical_cross_workspace_matrix(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        for query in (
            "@media (min-width: 1600px)",
            "@media (min-width: 1025px) and (max-width: 1279px)",
            "@media (min-width: 681px) and (max-width: 1024px)",
            "@media (max-width: 680px)",
            "@media (max-width: 420px)",
        ):
            self.assertIn(query, text)
        self.assertNotIn("@media (max-width: 760px)", text)
        self.assertNotIn("@media (max-width: 720px)", text)

    def test_tablet_rules_protect_dense_workspace_readability(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        tablet = text.split(
            "@media (min-width: 681px) and (max-width: 1024px)", 1
        )[1].split("@media (max-width: 680px)", 1)[0]
        self.assertIn(".review-board-workspace", tablet)
        self.assertIn("display: block !important", tablet)
        self.assertIn(".rrugc-grid.rrugc-masonry-grid", tablet)
        self.assertIn("repeat(4, minmax(0, 1fr))", tablet)
        self.assertIn(".ops-query-bar .ops-filters", tablet)
        self.assertIn(".ops-charts", tablet)

    def test_phone_rules_keep_primary_workspaces_single_column(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        phone = text.split("@media (max-width: 680px)", 1)[1].split(
            "@media (max-width: 420px)", 1
        )[0]
        self.assertIn(".review-board-filter-main", phone)
        self.assertIn(".ops-query-bar .ops-filters", phone)
        self.assertIn("grid-template-columns: 1fr !important", phone)
        self.assertIn(".rrugc-source-gallery", phone)

    def test_foundation_does_not_change_asset_explorer_input_focus_baseline(self) -> None:
        text = (ROOT / "apps/client/styles/ui-foundation.css").read_text(
            encoding="utf-8"
        )
        focus_block = text.split("/* Keep common overlays", 1)[0]
        self.assertNotIn("input", focus_block.split("Shared keyboard focus", 1)[1])


if __name__ == "__main__":
    unittest.main()
