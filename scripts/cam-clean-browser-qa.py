#!/usr/bin/env python3
"""Safely prune obsolete Browser QA evidence after successful production deploys.

Only known disposable directories inside apps/client/.ui-qa are eligible.
Visual baselines, failed QA reports, active sessions and product media are excluded.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import sys
import time


REPO = Path(__file__).resolve().parents[1]
QA_ROOT = REPO / "apps/client/.ui-qa"
CLIENT = REPO / "apps/client"
RUN_NAME = re.compile(r"^(?:20\d{2}-\d\d-\d\dT\d\d-\d\d-\d\d(?:-\d{3})?Z|20\d{6}T\d{6}Z)$")
PROPOSAL_NAME = re.compile(r"^20\d{6}T\d{6}Z-[\w-]+$")
HOUR = 3600
DAY = 24 * HOUR


def _read_json(file: Path) -> dict:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _evidence_failed(folder: Path) -> bool:
    report = _read_json(folder / "report.json")
    status = report.get("summary", {}).get("status") if isinstance(report.get("summary"), dict) else None
    if status is None:
        status = report.get("status")
    if status is None:
        visual = report.get("visual")
        if isinstance(visual, dict) and isinstance(visual.get("analysis"), dict):
            status = visual["analysis"].get("status")
    return str(status or "").lower() in {"failed", "fail", "error"}


def _size(folder: Path) -> int:
    total = 0
    for candidate in folder.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            total += candidate.stat().st_size
    return total


def _approved_baselines_committed(proposal: Path, client: Path) -> bool:
    acceptance = _read_json(proposal / "acceptance.json")
    report = _read_json(proposal / "report.json")
    screenshots = acceptance.get("changedScreenshots")
    visual = report.get("visual")
    relative = visual.get("baselineDir") if isinstance(visual, dict) else None
    if not isinstance(screenshots, list) or not screenshots or not isinstance(relative, str):
        return False
    baseline_root = (client / "visual-baselines").resolve()
    baseline_dir = (client / relative).resolve()
    if baseline_dir == baseline_root or baseline_root not in baseline_dir.parents:
        return False
    return all(isinstance(name, str) and Path(name).name == name
               and (baseline_dir / name).is_file() for name in screenshots)


def _safe_children(folder: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not folder.is_dir() or folder.is_symlink():
        return []
    return sorted(
        (p for p in folder.iterdir()
         if pattern.fullmatch(p.name) and p.is_dir() and not p.is_symlink()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )


def cleanup_browser_qa(root: Path, *, client: Path, now: float | None = None,
                       dry_run: bool = False, min_age_hours: int = 2) -> dict:
    if root.is_symlink():
        raise ValueError("Browser QA root must not be a symlink")
    root = root.resolve()
    now = time.time() if now is None else now
    if not root.is_dir() or root.name != ".ui-qa":
        raise ValueError("Browser QA cleanup requires a real .ui-qa directory")
    if min_age_hours < 1:
        raise ValueError("Minimum QA run age must be at least one hour")
    lock_path = root / ".cleanup.lock"
    changes: list[dict] = []
    counts = {"scanned": 0, "removed": 0, "bytes_reclaimed": 0, "protected_failures": 0}

    # Serializes multiple deploy-cleanups; only known complete old run folders
    # are considered, so current QA processes do not get their evidence deleted.
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rules = [
            ("qa-runs", root, RUN_NAME, 3, 3 * DAY),
            ("production-smoke", root / "production-smoke", RUN_NAME, 5, 0),
            ("baseline-proposals", root / "baseline-proposals", PROPOSAL_NAME, 2, 7 * DAY),
        ]
        for category, base, pattern, keep, minimum_retention in rules:
            candidates = _safe_children(base, pattern)
            for index, folder in enumerate(candidates):
                counts["scanned"] += 1
                age = now - folder.stat().st_mtime
                if index < keep or age < max(min_age_hours * HOUR, minimum_retention):
                    continue
                if category == "baseline-proposals":
                    # Accepted proposals can be removed once the permanent
                    # committed visual baseline copies have been verified.
                    if not _approved_baselines_committed(folder, client):
                        continue
                else:
                    if _evidence_failed(folder) and age < 14 * DAY:
                        counts["protected_failures"] += 1
                        continue
                    # Do not prune in-progress QA runs that have no finished
                    # report until at least 7 days old.
                    if not (folder / "report.json").is_file() and age < 7 * DAY:
                        continue
                if folder.is_symlink() or folder.parent.resolve() != base.resolve():
                    continue
                size = _size(folder)
                changes.append({"category": category, "name": folder.name, "bytes": size})
                counts["bytes_reclaimed"] += size
                counts["removed"] += 1
                if not dry_run:
                    shutil.rmtree(folder)
    return {**counts, "dry_run": dry_run, "changes": changes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-age-hours", type=int, default=2)
    args = parser.parse_args()
    if not QA_ROOT.is_dir():
        print("Browser QA directory does not exist; nothing to clean.")
        return 0
    result = cleanup_browser_qa(QA_ROOT, client=CLIENT, dry_run=args.dry_run,
                                min_age_hours=args.min_age_hours)
    label = "would remove" if args.dry_run else "removed"
    print(f"Browser QA cleanup: {label} {result['removed']} run folders; "
          f"{result['bytes_reclaimed'] / 1024**2:.1f} MiB; "
          f"protected failed runs: {result['protected_failures']}.")
    for change in result["changes"][:12]:
        print(f"  {change['category']}/{change['name']} ({change['bytes'] / 1024**2:.1f} MiB)")
    if len(result["changes"]) > 12:
        print(f"  ... and {len(result['changes']) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
