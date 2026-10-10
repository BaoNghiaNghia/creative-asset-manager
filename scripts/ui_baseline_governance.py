#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "apps" / "client"


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_state() -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    diff = _git("diff", "--binary", "HEAD", "--")
    tracked = [line for line in _git("diff", "--name-only", "HEAD", "--").splitlines() if line]
    untracked = sorted(
        line
        for line in _git("ls-files", "--others", "--exclude-standard").splitlines()
        if line
    )
    evidence = [f"head:{head}", "diff:", diff, "untracked:"]
    for rel in untracked:
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            continue
        evidence.append(f"{rel}:{sha256_file(target)}")
    fingerprint = hashlib.sha256("\n".join(evidence).encode()).hexdigest()
    return {
        "head": head,
        "fingerprint": fingerprint,
        "changedFiles": sorted(set(tracked + untracked)),
    }


def frontend_source_files(changed_files: list[str]) -> list[str]:
    return [
        file_path
        for file_path in changed_files
        if (
            (file_path.startswith("apps/client/") or file_path.startswith("packages/"))
            and not file_path.startswith("apps/client/dist/")
            and not file_path.startswith("apps/client/.ui-qa/")
            and not file_path.startswith("apps/client/scripts/")
            and not file_path.startswith("apps/client/visual-baselines/")
        )
    ]


def qa_profile_support_files(changed_files: list[str]) -> list[str]:
    allowed_exact = {
        "scripts/cam-ui-staging-qa.sh",
        "scripts/cam-ui-repair-check.sh",
        "scripts/cam-ui-autofix.sh",
        "scripts/cam-ui-baseline-propose.sh",
        "scripts/cam-ui-baseline-accept.sh",
        "scripts/ui_baseline_governance.py",
    }
    return [
        file_path
        for file_path in changed_files
        if (
            file_path in allowed_exact
            or file_path == "apps/client/scripts/ui-qa-profiles.mjs"
            or file_path == "apps/client/scripts/ui-qa-fixture.mjs"
            or file_path.startswith("apps/client/scripts/fixtures/")
            or file_path.startswith("docs/operations/ui-qa-")
        )
    ]


def expected_names(manifest: dict[str, Any]) -> set[str]:
    viewport_names = [
        item if isinstance(item, str) else item["name"]
        for item in manifest.get("viewports", [])
    ]
    states = manifest.get("states", [])
    return {f"{viewport}--{state}.png" for viewport in viewport_names for state in states}


def is_additive_manifest_extension(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Allow expanding an existing QA profile without weakening visual approvals."""
    old_viewports = {item["name"]: item for item in before.get("viewports", [])}
    new_viewports = {item["name"]: item for item in after.get("viewports", [])}
    old_states = before.get("states", [])
    new_states = after.get("states", [])
    if not old_viewports or not old_states:
        return False
    if len(new_viewports) != len(after.get("viewports", [])) or len(new_states) != len(set(new_states)):
        return False
    if not all(new_viewports.get(name) == value for name, value in old_viewports.items()):
        return False
    if not all(name in new_states for name in old_states):
        return False
    for name, value in new_viewports.items():
        if not isinstance(name, str) or not name:
            return False
        if any(not isinstance(value.get(key), int) or value[key] <= 0 for key in ("width", "height")):
            return False
    before_other = {k: v for k, v in before.items() if k not in ("states", "viewports")}
    after_other = {k: v for k, v in after.items() if k not in ("states", "viewports")}
    return before_other == after_other and (
        old_viewports != new_viewports or old_states != new_states
    )


def runtime_issue_count(report: dict[str, Any]) -> int:
    total = 0
    for result in report.get("results", []):
        issues = result.get("issues", {})
        total += sum(
            len(issues.get(key, []))
            for key in ("consoleErrors", "pageErrors", "requestFailures", "badResponses")
        )
    return total


def comparison_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for viewport_result in report.get("results", []):
        for comparison in viewport_result.get("visualComparisons", []):
            result[comparison["screenshot"]] = {
                "viewport": viewport_result.get("viewport"),
                **comparison,
            }
    return result


def render_markdown(proposal: dict[str, Any]) -> str:
    lines = [
        "# UI visual baseline proposal",
        "",
        f"Status: **{proposal['status'].upper()}**",
        f"Proposal: `{proposal['proposalId']}`",
        f"Task: {proposal['task']}",
        f"Source HEAD: `{proposal['source']['head']}`",
        f"Workspace fingerprint: `{proposal['source']['fingerprint']}`",
        "",
        "## Review summary",
        "",
        f"- Browser/runtime issues: **{proposal['runtimeIssueCount']}**",
        f"- Candidate screenshots: **{len(proposal['candidates'])}**",
        f"- Visual changes requiring review: **{len(proposal['changedScreenshots'])}**",
        f"- Frontend source files changed: **{len(proposal['source']['frontendSourceFiles'])}**",
        f"- Fixture/profile support files changed: **{len(proposal['source'].get('qaProfileSupportFiles', []))}**",
        f"- New profile bootstrap: **{'yes' if proposal.get('profileBootstrap') else 'no'}**",
        "",
    ]
    if proposal["changedScreenshots"]:
        lines.extend(["## Proposed baseline changes", ""])
        for item in proposal["changedScreenshots"]:
            lines.append(
                f"- `{item['name']}` — {item['status']}; "
                f"changed pixels: {item.get('mismatchedPixels', 'n/a')}; "
                f"ratio: {item.get('diffRatio', 'n/a')}"
            )
        lines.append("")
    if proposal["source"]["frontendSourceFiles"]:
        lines.extend(["## Source changes tied to this proposal", ""])
        lines.extend(f"- `{file_path}`" for file_path in proposal["source"]["frontendSourceFiles"])
        lines.append("")
    lines.extend(
        [
            "## Governance",
            "",
            "This proposal does **not** modify tracked baselines.",
            "Accept it only after confirming the visual changes match the user's intended design.",
            "Never accept a proposal merely to make the visual gate pass.",
            "",
        ]
    )
    return "\n".join(lines)


def create_proposal(run_dir: Path, baseline_dir: Path, proposal_dir: Path, task: str) -> dict[str, Any]:
    if not task.strip():
        raise SystemExit("ERROR: Baseline proposal requires a non-empty UI task.")

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    manifest_path = baseline_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = expected_names(manifest)
    state = workspace_state()
    state["frontendSourceFiles"] = frontend_source_files(state["changedFiles"])
    state["qaProfileSupportFiles"] = qa_profile_support_files(state["changedFiles"])
    baseline_rel = baseline_dir.relative_to(ROOT).as_posix()
    bootstrap_profile = not any(baseline_dir.glob("*.png"))
    target_baseline_changes = [
        file_path
        for file_path in state["changedFiles"]
        if file_path.startswith(baseline_rel + "/")
    ]

    if not state["frontendSourceFiles"] and not (
        bootstrap_profile and state["qaProfileSupportFiles"]
    ):
        raise SystemExit(
            "ERROR: Refusing baseline proposal without a frontend source change or a new fixture-backed QA profile bootstrap."
        )

    allowed_manifest_change = baseline_rel + "/manifest.json"
    additive_extension = False
    if allowed_manifest_change in target_baseline_changes and not bootstrap_profile:
        try:
            original = json.loads(_git("show", f"HEAD:{allowed_manifest_change}"))
            additive_extension = is_additive_manifest_extension(original, manifest)
        except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            additive_extension = False
    unexpected_target_changes = [
        file_path
        for file_path in target_baseline_changes
        if not (
            file_path == allowed_manifest_change
            and (bootstrap_profile or additive_extension)
        )
    ]
    if unexpected_target_changes:
        raise SystemExit(
            "ERROR: Target visual baselines are already modified. Restore them before creating a governed proposal."
        )

    candidate_dir = proposal_dir / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    for result in report.get("results", []):
        for screenshot in result.get("screenshots", []):
            if screenshot not in expected:
                raise SystemExit(
                    f"ERROR: Candidate screenshot {screenshot} is outside the tracked baseline manifest. "
                    "Update the visual profile explicitly before proposing a baseline change."
                )
            shutil.copy2(run_dir / screenshot, candidate_dir / screenshot)
            names.add(screenshot)

    comparisons = comparison_map(report)
    candidates: list[dict[str, Any]] = []
    for name in sorted(names):
        baseline_path = baseline_dir / name
        comparison = comparisons.get(name, {})
        candidates.append(
            {
                "name": name,
                "candidateHash": sha256_file(candidate_dir / name),
                "baselineHash": sha256_file(baseline_path) if baseline_path.exists() else None,
                "status": comparison.get("status", "unknown"),
                "mismatchedPixels": comparison.get("mismatchedPixels"),
                "diffRatio": comparison.get("diffRatio"),
            }
        )

    changed = [
        item
        for item in candidates
        if item["status"] in {"mismatch", "dimension-mismatch", "missing-baseline"}
    ]
    issues = runtime_issue_count(report)
    status = "blocked" if issues else ("review-required" if changed else "no-change")
    proposal = {
        "schemaVersion": 1,
        "proposalId": proposal_dir.name,
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "task": task.strip(),
        "source": state,
        "profileBootstrap": bootstrap_profile,
        "baseline": {
            "directory": baseline_rel,
            "manifestHash": sha256_file(manifest_path),
        },
        "run": {
            "directory": run_dir.relative_to(ROOT).as_posix(),
            "mode": report.get("mode", "full"),
            "states": report.get("states", []),
            "viewports": [item.get("viewport") for item in report.get("results", [])],
        },
        "runtimeIssueCount": issues,
        "candidates": candidates,
        "changedScreenshots": changed,
    }
    proposal_dir.mkdir(parents=True, exist_ok=True)
    (proposal_dir / "proposal.json").write_text(
        json.dumps(proposal, indent=2) + "\n", encoding="utf-8"
    )
    (proposal_dir / "proposal.md").write_text(render_markdown(proposal) + "\n", encoding="utf-8")
    shutil.copy2(run_dir / "report.json", proposal_dir / "report.json")
    analysis_path = run_dir / "visual-analysis.json"
    if analysis_path.exists():
        shutil.copy2(analysis_path, proposal_dir / "visual-analysis.json")
    return proposal


def validate_proposal(proposal_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    proposal = json.loads((proposal_dir / "proposal.json").read_text(encoding="utf-8"))
    if proposal.get("status") != "review-required":
        raise SystemExit(
            f"ERROR: Proposal status must be review-required, got {proposal.get('status')}."
        )
    if proposal.get("runtimeIssueCount") != 0:
        raise SystemExit("ERROR: Cannot accept a baseline proposal with Browser/runtime issues.")
    if not proposal.get("changedScreenshots"):
        raise SystemExit("ERROR: Cannot accept a baseline proposal with no visual changes.")

    current = workspace_state()
    source = proposal["source"]
    if current["head"] != source["head"]:
        raise SystemExit("ERROR: Proposal source HEAD no longer matches the current workspace.")
    if current["fingerprint"] != source["fingerprint"]:
        raise SystemExit(
            "ERROR: Workspace changed after the baseline proposal was created. Generate a new proposal."
        )

    baseline_dir = (ROOT / proposal["baseline"]["directory"]).resolve()
    manifest_path = baseline_dir / "manifest.json"
    if sha256_file(manifest_path) != proposal["baseline"]["manifestHash"]:
        raise SystemExit("ERROR: Tracked baseline manifest changed after proposal creation.")

    expected = expected_names(json.loads(manifest_path.read_text(encoding="utf-8")))
    for item in proposal.get("candidates", []):
        name = item["name"]
        if name not in expected:
            raise SystemExit(f"ERROR: Unexpected candidate baseline name: {name}")
        candidate = proposal_dir / "candidate" / name
        if sha256_file(candidate) != item["candidateHash"]:
            raise SystemExit(f"ERROR: Candidate screenshot changed after proposal: {name}")
        baseline = baseline_dir / name
        current_hash = sha256_file(baseline) if baseline.exists() else None
        if current_hash != item["baselineHash"]:
            raise SystemExit(f"ERROR: Tracked baseline changed after proposal: {name}")
    return proposal, baseline_dir, manifest_path


def apply_proposal(proposal_dir: Path, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise SystemExit("ERROR: Baseline acceptance requires a non-empty acceptance reason.")
    proposal, baseline_dir, manifest_path = validate_proposal(proposal_dir)
    changed_names = {item["name"] for item in proposal["changedScreenshots"]}
    for item in proposal["candidates"]:
        if item["name"] in changed_names:
            shutil.copy2(proposal_dir / "candidate" / item["name"], baseline_dir / item["name"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    accepted_at = datetime.now(timezone.utc).isoformat()
    manifest["governance"] = {
        "lastAcceptedProposalId": proposal["proposalId"],
        "task": proposal["task"],
        "acceptanceReason": reason.strip(),
        "sourceHead": proposal["source"]["head"],
        "workspaceFingerprint": proposal["source"]["fingerprint"],
        "acceptedAt": accepted_at,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    acceptance = {
        "schemaVersion": 1,
        "proposalId": proposal["proposalId"],
        "acceptedAt": accepted_at,
        "acceptanceReason": reason.strip(),
        "changedScreenshots": sorted(changed_names),
        "sourceHead": proposal["source"]["head"],
        "workspaceFingerprint": proposal["source"]["fingerprint"],
    }
    (proposal_dir / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2) + "\n", encoding="utf-8"
    )
    return acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create")
    create.add_argument("--run-dir", required=True, type=Path)
    create.add_argument("--baseline-dir", required=True, type=Path)
    create.add_argument("--proposal-dir", required=True, type=Path)
    create.add_argument("--task", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--proposal-dir", required=True, type=Path)

    apply = sub.add_parser("apply")
    apply.add_argument("--proposal-dir", required=True, type=Path)
    apply.add_argument("--reason", required=True)

    args = parser.parse_args()
    if args.action == "create":
        result = create_proposal(
            args.run_dir.resolve(),
            args.baseline_dir.resolve(),
            args.proposal_dir.resolve(),
            args.task,
        )
    elif args.action == "validate":
        result = validate_proposal(args.proposal_dir.resolve())[0]
    else:
        result = apply_proposal(args.proposal_dir.resolve(), args.reason)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
