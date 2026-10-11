#!/usr/bin/env python3
"""Conservative post-reboot VPS recovery: audit and repair one obsolete swap entry.

--audit is read-only. --apply-swap is explicitly opt-in, root-only and changes
only a legacy /swapfile entry in /etc/fstab. Network routes, production services,
the active CAM swap, application data and secrets are never modified.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


FSTAB = Path("/etc/fstab")
OLD_SWAP = "/swapfile"
ACTIVE_SWAP = "/swapfile-camqa"


def legacy_entry_indices(text: str) -> list[int]:
    """Match the exact fstab source and swap filesystem, never a filename prefix."""
    found = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 4 and fields[0] == OLD_SWAP and fields[2] == "swap":
            found.append(index)
    return found


def active_swap_names() -> set[str]:
    result = subprocess.run(
        ["swapon", "--show=NAME", "--noheadings", "--raw"],
        capture_output=True, text=True, check=True,
    )
    return {s.strip() for s in result.stdout.splitlines() if s.strip()}


def validate_fstab(text: str, *, swaps: set[str]) -> list[int]:
    if ACTIVE_SWAP not in swaps:
        raise RuntimeError("Active CAM 2 GiB swap is absent. Refusing to edit fstab.")
    if not any(
        line.split()[:1] == [ACTIVE_SWAP] and len(line.split()) >= 3
        and line.split()[2] == "swap"
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ):
        raise RuntimeError("Active CAM swap has no live fstab entry. Refusing change.")
    indices = legacy_entry_indices(text)
    if len(indices) > 1:
        raise RuntimeError("Multiple legacy swap entries; manual inspection required.")
    return indices


def rewrite_old_swap(text: str) -> str:
    entries = legacy_entry_indices(text)
    if not entries:
        return text
    if len(entries) != 1:
        raise RuntimeError("Multiple legacy swap entries; manual inspection required.")
    lines = text.splitlines(keepends=True)
    lines[entries[0]] = "# CAM 2026-10-11: invalid legacy swap retired; " + lines[entries[0]]
    return "".join(lines)


def repair_fstab(path: Path, *, swaps: set[str]) -> Path | None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("fstab must be a regular, non-symlink file.")
    original_stat = path.stat()
    if not stat.S_ISREG(original_stat.st_mode):
        raise RuntimeError("Refusing non-regular fstab.")
    original = path.read_text(encoding="utf-8")
    legacy_indices = validate_fstab(original, swaps=swaps)
    if not legacy_indices:
        return None

    updated = rewrite_old_swap(original)
    if updated == original or "/swapfile" not in original:
        raise RuntimeError("No safe, auditable fstab change could be prepared.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(path.name + ".cam-pre-swap-" + stamp)
    if backup.exists():
        raise RuntimeError("Backup collision; refusing to overwrite an existing file.")
    shutil.copy2(path, backup, follow_symlinks=False)
    fd, temporary = tempfile.mkstemp(prefix=".fstab.cam-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(original_stat.st_mode))
            os.fchown(handle.fileno(), original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def command_summary(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=12)
        output = (result.stdout or result.stderr).strip()
        return result.returncode, output[:900]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, type(exc).__name__


def audit() -> None:
    print("== Post-reboot audit (read-only) ==")
    for title, argv in [
        ("Kernel", ["uname", "-r"]),
        ("Swap", ["swapon", "--show"]),
        ("fstab swap", ["findmnt", "--fstab", "--types", "swap"]),
        ("IPv4 route", ["ip", "-4", "route", "show", "default"]),
        ("IPv6 route", ["ip", "-6", "route", "show", "default"]),
        ("eth0", ["networkctl", "list", "--no-pager"]),
        ("Disk", ["df", "-h"]),
    ]:
        code, output = command_summary(argv)
        print(f"\n{title} (exit={code}):\n{output or '(none)'}")
    print("\nNo OS changes were made.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--audit", action="store_true")
    group.add_argument("--apply-swap", action="store_true")
    args = parser.parse_args()
    if args.audit:
        audit()
        return 0
    if os.geteuid() != 0:
        print("ERROR: --apply-swap requires an explicit root/sudo invocation.", file=sys.stderr)
        return 2
    try:
        backups = active_swap_names()
        backup = repair_fstab(FSTAB, swaps=backups)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if backup is None:
        print("PASS: No active legacy /swapfile entry. Nothing changed.")
        return 0
    print(f"PASS: Retired invalid legacy /swapfile entry. Backup: {backup}")
    # Does not activate, deactivate or replace active swap.
    for argv in [
        ["systemctl", "daemon-reload"],
        ["systemctl", "reset-failed", "swapfile.swap"],
    ]:
        code, output = command_summary(argv)
        print(f"{' '.join(argv)}: {'PASS' if code == 0 else 'WARNING'} {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
