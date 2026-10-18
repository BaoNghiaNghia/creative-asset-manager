#!/usr/bin/env python3
"""Dry-run by default; stop only old, orphaned, repo-local Vite QA previews.

Never targets Nginx, API, Docker, user-running previews, or any public listener.
An explicit --apply revalidates PID, PPID, arguments and process cwd.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

REPO = Path(__file__).resolve().parents[1]
MIN_AGE_SECONDS = 600


def _preview_command(command: str) -> tuple[str, ...] | None:
    try:
        argv = shlex.split(command.strip())
    except ValueError:
        return None
    if len(argv) != 8 or argv[0] != "node" or argv[2:6] != [
        "preview", "--host", "127.0.0.1", "--port"
    ] or argv[7] != "--strictPort":
        return None
    program = argv[1]
    relative = {
        "./apps/client/node_modules/.bin/vite",
        "./node_modules/.bin/vite",
        "node_modules/.bin/vite",
    }
    # Historical pnpm form is allowed only under this project's dependencies.
    pnpm = (
        program.startswith((
            "./node_modules/.pnpm/vite@",
            "./apps/client/node_modules/.pnpm/vite@",
            str(REPO / "node_modules/.pnpm/vite@"),
            str(REPO / "apps/client/node_modules/.pnpm/vite@"),
        ))
        and program.endswith("/node_modules/vite/bin/vite.js")
    )
    absolute = Path(program).is_absolute() and (
        program.startswith(str(REPO / "apps/client/node_modules") + "/")
        or program.startswith(str(REPO / "node_modules") + "/")
    )
    if program not in relative and not pnpm and not absolute:
        return None
    try:
        port = int(argv[6])
    except ValueError:
        return None
    if not 1024 <= port <= 65535 or str(port) != argv[6]:
        return None
    return tuple(argv)


def _parse_report(report: str, min_age_seconds: int) -> list[tuple[int, tuple[str, ...]]]:
    found: list[tuple[int, tuple[str, ...]]] = []
    for line in report.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) != 4:
            continue
        try:
            pid, ppid, age = map(int, fields[:3])
        except ValueError:
            continue
        if pid <= 1 or ppid != 1 or age < min_age_seconds:
            continue
        argv = _preview_command(fields[3])
        if argv:
            found.append((pid, argv))
    return found


def orphaned_vite_servers(min_age_seconds: int = MIN_AGE_SECONDS, *,
                          report: str | None = None) -> list[int]:
    if min_age_seconds < MIN_AGE_SECONDS:
        raise ValueError("Minimum age must be at least 600 seconds")
    if report is None:
        report = subprocess.check_output(
            ["ps", "-ww", "-eo", "pid=,ppid=,etimes=,args="], text=True
        )
    return [pid for pid, _argv in _parse_report(report, min_age_seconds)]


def _safe_process_cwd(pid: int) -> bool:
    try:
        cwd = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return cwd in {REPO, REPO / "apps/client"}


def _still_same_orphan(pid: int, original: tuple[str, ...],
                       min_age_seconds: int = MIN_AGE_SECONDS) -> bool:
    try:
        current = subprocess.check_output(
            ["ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,etimes=,args="],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return (
        any(current_pid == pid and argv == original
            for current_pid, argv in _parse_report(current, min_age_seconds))
        and _safe_process_cwd(pid)
    )


def cleanup(*, apply: bool, min_age_seconds: int = MIN_AGE_SECONDS) -> int:
    if min_age_seconds < MIN_AGE_SECONDS:
        raise ValueError("Minimum age must be at least 600 seconds")
    report = subprocess.check_output(
        ["ps", "-ww", "-eo", "pid=,ppid=,etimes=,args="], text=True
    )
    candidates = _parse_report(report, min_age_seconds)
    affected = 0
    for pid, argv in candidates:
        if apply and not _still_same_orphan(pid, argv, min_age_seconds):
            print(f"skipped pid={pid}: identity or workspace changed")
            continue
        print(f"{'stopping' if apply else 'would-stop'} orphaned Vite QA preview pid={pid}")
        if apply:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        affected += 1
    if apply and affected:
        time.sleep(1)
    print(f"stale_vite_qa_servers={affected} mode={'apply' if apply else 'dry-run'}")
    return affected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Explicitly terminate matching old orphaned QA previews")
    parser.add_argument("--min-age-seconds", type=int, default=MIN_AGE_SECONDS)
    args = parser.parse_args()
    cleanup(apply=args.apply, min_age_seconds=args.min_age_seconds)
