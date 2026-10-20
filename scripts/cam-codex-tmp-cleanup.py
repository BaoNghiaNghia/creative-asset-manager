#!/usr/bin/env python3
"""Fail-closed cleanup of stale, EMPTY temporary Codex files/dirs only.

Never touches generated_images, auth.json, sessions, skills, plugins, SQLite,
cache, or any files with content. Default dry-run, no external dependencies.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import stat
import time

HOME_ROOTS = (
    Path("/var/lib/creative-asset-manager/codex"),
    Path("/var/lib/creative-asset-manager/codex-account-2"),
)

def codex_is_running() -> bool:
    """Never modify a Codex tmp tree during a Codex process."""
    proc = Path("/proc")
    try:
        pids = [x for x in proc.iterdir() if x.name.isdigit()]
    except OSError:
        return True
    for pid in pids:
        try:
            procname = (pid / "comm").read_text(encoding="utf-8").strip().lower()
            if "codex" in procname:
                return True
        except (OSError, UnicodeError):
            continue
    return False

def inspect(root: Path, cutoff: float, apply: bool, max_delete: int) -> tuple[int, int, int, int]:
    """Return stale zero-file count, empty-dir count, candidate blocks, removed count."""
    if not root.is_dir() or root.is_symlink() or root.resolve() != root:
        return (0, 0, 0, 0)
    files = dirs = savings = removed = 0
    deletable: set[str] = set()
    # file and directory contents are never opened/read.
    for path, foldernames, filenames in os.walk(root, topdown=False, followlinks=False):
        base = Path(path)
        if base.is_symlink() or base == root and not root.is_dir():
            continue
        try:
            initial_dir_stat = base.lstat()
        except OSError:
            continue
        for filename in filenames:
            entry = base / filename
            try:
                metadata = entry.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 0:
                    continue
                if metadata.st_mtime >= cutoff or metadata.st_nlink != 1:
                    continue
                files += 1
                savings += metadata.st_blocks * 512
                if not apply:
                    deletable.add(str(entry))
                elif removed < max_delete:
                    if removed % 128 == 0 and codex_is_running():
                        print("Codex process appeared; stopping without further changes.")
                        return (files, dirs, savings, removed)
                    verify = entry.lstat()
                    if (stat.S_ISREG(verify.st_mode) and verify.st_ino == metadata.st_ino
                            and verify.st_size == 0 and verify.st_mtime < cutoff):
                        entry.unlink()
                        removed += 1
                        deletable.add(str(entry))
            except OSError:
                continue
        if base == root:
            continue
        try:
            metadata = base.lstat()
            if (not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_ino != initial_dir_stat.st_ino
                    or initial_dir_stat.st_mtime >= cutoff):
                continue
            # Predict emptiness after eligible old zero-byte files and
            # children are removed. APPLY always rechecks actual emptiness.
            contents = list(base.iterdir())
            eligible_empty = all(str(child) in deletable for child in contents)
            if eligible_empty:
                dirs += 1
                savings += metadata.st_blocks * 512
                if not apply:
                    deletable.add(str(base))
                elif removed < max_delete and not contents:
                    if removed % 128 == 0 and codex_is_running():
                        print("Codex process appeared; stopping.")
                        return (files, dirs, savings, removed)
                    base.rmdir()  # fails safely if another process populated it
                    removed += 1
                    deletable.add(str(base))
        except OSError:
            continue
    return files, dirs, savings, removed

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Remove eligible empty temp files/dirs")
    parser.add_argument("--older-hours", type=int, default=48)
    parser.add_argument("--max-delete", type=int, default=25000)
    args = parser.parse_args()
    if not 24 <= args.older_hours <= 2160 or not 1 <= args.max_delete <= 100000:
        parser.error("older-hours must be 24-2160 and max-delete 1-100000")
    if args.apply and os.geteuid() != 0:
        parser.error("--apply requires root")
    if args.apply and codex_is_running():
        print("BLOCKED: Codex currently running. No changes made.")
        return 1
    cutoff = time.time() - args.older_hours * 3600
    print(f"Codex tmp-only cleanup: {'APPLY' if args.apply else 'DRY-RUN'} (age >{args.older_hours}h)")
    total = 0
    for home in HOME_ROOTS:
        tmp = home / "tmp"
        files, dirs, savings, removed = inspect(tmp, cutoff, args.apply, args.max_delete - total)
        print(f"{home.name}: stale-zero-files={files}, already-empty-folders={dirs}, "
              f"estimated={savings/1024/1024:.1f} MiB, removed={removed}")
        total += removed
    print("Protected: all image PNGs, authentication, sessions, plugins, skills, SQLite and cache.")
    if not args.apply:
        print("Dry-run: nothing deleted. Rerun with --apply if no Codex process is active.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
