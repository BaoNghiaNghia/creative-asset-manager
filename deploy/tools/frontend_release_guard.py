#!/usr/bin/env python3
"""Fail closed before activating a divergent or incomplete frontend release."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40,64}")
CONTRACT = "cam-color-mix-v5"
MIN_REVISION = 5


def read_build(dist: Path) -> dict:
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    if not isinstance(info, dict) or not SHA.fullmatch(str(info.get("build_commit", ""))):
        raise ValueError("Invalid frontend build provenance")
    return info


def validate_assets(dist: Path) -> None:
    info = read_build(dist)
    if info.get("ui_contract") != CONTRACT or info.get("ui_revision") != MIN_REVISION:
        raise ValueError("Required CAM Color Mix V5 contract is missing")
    assets = dist / "assets"
    if not any(path.stat().st_size > 0 for path in assets.glob("inter-latin-wght-normal-*.woff2")):
        raise ValueError("Inter Variable font is missing")
    if not any(path.stat().st_size > 0 for path in assets.glob("be-vietnam-pro-latin-400-normal-*.woff2")):
        raise ValueError("Be Vietnam Pro font is missing")
    css = "\n".join(path.read_text(encoding="utf-8") for path in assets.glob("*.css"))
    for token in ("--cam-icon-blue:", "--cam-icon-mint:", "--cam-icon-orange:", "--cam-nav-font:", "--etsy-demand-column:", ".etsy-demand-metrics"):
        if token not in css:
            raise ValueError(f"Required frontend visual contract missing: {token}")


def validate_release(candidate: Path, active: Path, source: Path, commit: str) -> None:
    validate_assets(candidate)
    if not SHA.fullmatch(commit):
        raise ValueError("Invalid candidate release commit")
    if not active.exists():
        return
    info = read_build(active)
    active_commit = info["build_commit"]
    # A branch that cannot see or does not descend from the live frontend
    # must integrate the live source before activation. Explicit rollback
    # uses the separate rollback workflow, never this normal deployment.
    result = subprocess.run(
        ["git", "-C", str(source), "merge-base", "--is-ancestor", active_commit, commit],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Candidate does not contain live frontend build {active_commit}; merge first")
    revision = info.get("ui_revision", 0)
    if type(revision) is not int or revision > MIN_REVISION:
        raise ValueError("Candidate would downgrade the live UI revision")


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("Usage: frontend_release_guard.py CANDIDATE_DIST ACTIVE_DIST SOURCE COMMIT", file=sys.stderr)
        return 2
    try:
        validate_release(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4])
    except (OSError, ValueError, TypeError) as exc:
        print(f"Frontend Release Guard: {exc}", file=sys.stderr)
        return 1
    print("Frontend Release Guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
