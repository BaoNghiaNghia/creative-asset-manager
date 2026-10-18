#!/usr/bin/env python3
"""Read-only CAM release inventory. Prints candidates; never deletes releases.

Run against production only from an operator-authorized shell. This script
does not grant access to /opt or /var/www from a restricted workspace.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import subprocess
from pathlib import Path

RELEASE_ID = re.compile(r"[a-f0-9]{40}(?:-[a-zA-Z0-9][a-zA-Z0-9_.-]*)?\Z")
PROTECTED_LINKS = ("current", "previous")


class InventoryError(RuntimeError):
    pass


def _release_from_path(path: Path, releases: Path) -> str | None:
    try:
        relative = path.resolve(strict=False).relative_to(releases)
    except (ValueError, OSError, RuntimeError):
        return None
    if not relative.parts:
        return None
    release = relative.parts[0]
    if RELEASE_ID.fullmatch(release) and (releases / release).is_dir():
        return release
    return None


def _process_release_references(releases: Path, *, proc_root: Path = Path("/proc")) -> tuple[dict[str, list[str]], bool]:
    """Return references and whether any live process paths were unreadable.

    A permission-denied /proc read must not result in an apparently clean
    candidate. ENOENT/ESRCH are expected for kernel threads/exited processes.
    """
    references: dict[str, list[str]] = {}
    if not proc_root.is_dir():
        raise InventoryError("Process table unavailable: cannot safely classify release protection.")
    complete = True
    try:
        processes = list(proc_root.iterdir())
    except OSError as exc:
        raise InventoryError("Process table enumeration failed.") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        for name in ("cwd", "exe"):
            link = process / name
            try:
                target = Path(os.readlink(link))
            except OSError as exc:
                if isinstance(exc, FileNotFoundError) or exc.errno in (errno.ENOENT, errno.ESRCH):
                    continue
                complete = False
                continue
            release = _release_from_path(target, releases)
            if release:
                references.setdefault(release, []).append(f"pid_{process.name}_{name}")
    return references, complete


def _directory_mib(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["du", "-sk", "--", str(path)],
            check=False, capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        return round(int(result.stdout.split(maxsplit=1)[0]) / 1024, 1)
    except (ValueError, IndexError):
        return None


def inventory(root: Path, *, proc_root: Path = Path("/proc")) -> dict:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise InventoryError("Application release root must be a real existing directory.")
    releases = root / "releases"
    if releases.is_symlink() or not releases.is_dir():
        raise InventoryError("Releases directory missing or symlinked.")
    releases = releases.resolve(strict=True)

    protections: dict[str, list[str]] = {}
    warnings: list[str] = []
    for name in PROTECTED_LINKS:
        link = root / name
        if not link.exists() and not link.is_symlink():
            warnings.append(f"{name}_symlink_missing")
            continue
        if not link.is_symlink():
            raise InventoryError(f"{name} must be a release symlink.")
        try:
            target = link.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InventoryError(f"{name} symlink is broken.") from exc
        release = _release_from_path(target, releases)
        if not release or target != releases / release:
            raise InventoryError(f"{name} does not point to a canonical immutable release.")
        protections.setdefault(release, []).append(name)

    if "current" not in [r for why in protections.values() for r in why]:
        raise InventoryError("No valid current release symlink: inventory is blocked.")

    rollback_ready = "previous_symlink_missing" not in warnings
    process_refs, scan_complete = _process_release_references(releases, proc_root=proc_root)
    if not scan_complete:
        warnings.append("process_references_incomplete")
    for release, reasons in process_refs.items():
        protections.setdefault(release, []).extend(reasons)

    entries: list[dict] = []
    for entry in sorted(releases.iterdir(), key=lambda p: p.name):
        if entry.is_symlink() or not entry.is_dir() or not RELEASE_ID.fullmatch(entry.name):
            warnings.append("unexpected_release_entry_present")
            continue
        reasons = sorted(set(protections.get(entry.name, [])))
        size_mib = _directory_mib(entry)
        if size_mib is None:
            warnings.append("release_size_unavailable")
        entries.append({
            "release": entry.name,
            "protected_by": reasons,
            "classification": (
                "PROTECTED" if reasons
                else "REVIEW_ONLY" if scan_complete and rollback_ready and size_mib is not None
                else "UNKNOWN"
            ),
            "size_mib": size_mib,
        })
    return {
        "READ_ONLY": True, "DELETION_PERFORMED": False, "ROOT": str(root),
        "release_count": len(entries),
        "protected_count": sum(e["classification"] == "PROTECTED" for e in entries),
        "review_count": sum(e["classification"] == "REVIEW_ONLY" for e in entries),
        "unknown_count": sum(e["classification"] == "UNKNOWN" for e in entries),
        "PROCESS_SCAN": "COMPLETE" if scan_complete else "INCOMPLETE",
        "ROLLBACK_POINTERS": "READY" if rollback_ready else "INCOMPLETE",
        "releases": entries, "warnings": sorted(set(warnings)),
        "REVIEW_REQUIRED_FOR_ANY_DELETION": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, default=Path("/opt/creative-asset-manager"))
    parser.add_argument("--frontend-root", type=Path, default=Path("/var/www/creative-asset-manager"))
    args = parser.parse_args()
    try:
        report = {
            "backend": inventory(args.backend_root),
            "frontend": inventory(args.frontend_root),
        }
    except InventoryError as exc:
        print(json.dumps({"READ_ONLY": True, "STATUS": "BLOCKED", "reason": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
