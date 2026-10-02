from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/ui_baseline_governance.py"
SPEC = importlib.util.spec_from_file_location("ui_baseline_governance", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class UiBaselineGovernanceTests(unittest.TestCase):
    def test_expected_names_follow_manifest_matrix(self) -> None:
        manifest = {
            "viewports": [{"name": "desktop"}, {"name": "mobile"}],
            "states": ["default", "selected-hover"],
        }
        self.assertEqual(
            MODULE.expected_names(manifest),
            {
                "desktop--default.png",
                "desktop--selected-hover.png",
                "mobile--default.png",
                "mobile--selected-hover.png",
            },
        )

    def test_frontend_source_files_excludes_automation_and_baselines(self) -> None:
        files = MODULE.frontend_source_files(
            [
                "apps/client/app/components/AssetGrid.tsx",
                "apps/client/scripts/ui-qa.mjs",
                "apps/client/visual-baselines/explorer-viewer/mobile--default.png",
                "apps/client/dist/build-info.json",
                "scripts/cam-ui-gate.sh",
            ]
        )
        self.assertEqual(files, ["apps/client/app/components/AssetGrid.tsx"])

    def test_runtime_issue_count_ignores_intentional_aborts(self) -> None:
        report = {
            "results": [
                {
                    "issues": {
                        "consoleErrors": ["boom"],
                        "pageErrors": [],
                        "requestFailures": [{"url": "x"}],
                        "ignoredRequestFailures": [{"error": "net::ERR_ABORTED"}],
                        "badResponses": [],
                    }
                }
            ]
        }
        self.assertEqual(MODULE.runtime_issue_count(report), 2)

    def test_markdown_makes_review_boundary_explicit(self) -> None:
        proposal = {
            "status": "review-required",
            "proposalId": "proposal-1",
            "task": "Increase title size",
            "source": {
                "head": "abc123",
                "fingerprint": "fingerprint",
                "frontendSourceFiles": ["apps/client/app/components/AssetGrid.tsx"],
            },
            "runtimeIssueCount": 0,
            "candidates": [{"name": "desktop--default.png"}],
            "changedScreenshots": [
                {
                    "name": "desktop--default.png",
                    "status": "mismatch",
                    "mismatchedPixels": 100,
                    "diffRatio": 0.01,
                }
            ],
        }
        markdown = MODULE.render_markdown(proposal)
        self.assertIn("does **not** modify tracked baselines", markdown)
        self.assertIn("Never accept a proposal merely to make the visual gate pass", markdown)
        self.assertIn("AssetGrid.tsx", markdown)

    def test_apply_requires_acceptance_reason_before_mutation(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            MODULE.apply_proposal(Path("/does/not/matter"), "")
        self.assertIn("acceptance reason", str(ctx.exception))

    def test_apply_valid_proposal_updates_only_candidate_and_governance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_dir = root / "apps/client/visual-baselines/explorer-viewer"
            proposal_dir = root / "proposal"
            candidate_dir = proposal_dir / "candidate"
            baseline_dir.mkdir(parents=True)
            candidate_dir.mkdir(parents=True)

            name = "desktop--default.png"
            baseline_file = baseline_dir / name
            candidate_file = candidate_dir / name
            baseline_file.write_bytes(b"old-baseline")
            candidate_file.write_bytes(b"new-approved-baseline")
            manifest_path = baseline_dir / "manifest.json"
            manifest = {
                "schemaVersion": 1,
                "viewports": [{"name": "desktop"}],
                "states": ["default"],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            workspace = {
                "head": "abc123",
                "fingerprint": "workspace-fingerprint",
                "changedFiles": ["apps/client/app/components/AssetGrid.tsx"],
            }
            proposal = {
                "schemaVersion": 1,
                "proposalId": "proposal-1",
                "status": "review-required",
                "task": "User-approved card redesign",
                "source": {
                    **workspace,
                    "frontendSourceFiles": [
                        "apps/client/app/components/AssetGrid.tsx"
                    ],
                },
                "baseline": {
                    "directory": "apps/client/visual-baselines/explorer-viewer",
                    "manifestHash": MODULE.sha256_file(manifest_path),
                },
                "runtimeIssueCount": 0,
                "candidates": [
                    {
                        "name": name,
                        "candidateHash": MODULE.sha256_file(candidate_file),
                        "baselineHash": MODULE.sha256_file(baseline_file),
                        "status": "mismatch",
                        "mismatchedPixels": 20,
                        "diffRatio": 0.01,
                    }
                ],
                "changedScreenshots": [{"name": name, "status": "mismatch"}],
            }
            (proposal_dir / "proposal.json").write_text(
                json.dumps(proposal), encoding="utf-8"
            )

            with (
                mock.patch.object(MODULE, "ROOT", root),
                mock.patch.object(MODULE, "workspace_state", return_value=workspace),
            ):
                acceptance = MODULE.apply_proposal(
                    proposal_dir, "Matches the explicit approved redesign"
                )

            self.assertEqual(baseline_file.read_bytes(), b"new-approved-baseline")
            updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                updated_manifest["governance"]["lastAcceptedProposalId"],
                "proposal-1",
            )
            self.assertEqual(
                updated_manifest["governance"]["acceptanceReason"],
                "Matches the explicit approved redesign",
            )
            self.assertEqual(acceptance["changedScreenshots"], [name])


if __name__ == "__main__":
    unittest.main()
