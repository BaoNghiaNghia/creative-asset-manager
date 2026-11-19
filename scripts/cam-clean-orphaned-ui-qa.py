#!/usr/bin/env python3
"""Report or terminate orphaned loopback Vite QA servers, never production services."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time


def orphaned_vite_servers(min_age_seconds: int = 48 * 3600, *, report: str | None = None) -> list[int]:
    if report is None:
        report = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid=,etimes=,args="], text=True
        )
    matches = []
    for line in report.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) != 4:
            continue
        pid_text, ppid_text, age_text, command = fields
        if not (pid_text.isdecimal() and ppid_text.isdecimal()
                and age_text.isdecimal()):
            continue
        # The old scanner only recognized PNPM's internal vite.js path. CAM's
        # week-old QA previews also use npm's node_modules/.bin/vite launcher.
        # Strictly require a detached, loopback-only, explicit-port process
        # from a named CAM task worktree. Never touch live nginx/API/Chrome.
        vite_executable = (
            "node_modules/.bin/vite" in command
            or ("node_modules/.pnpm/vite@" in command
                and "vite/bin/vite.js" in command)
        )
        if (
            ppid_text != "1"
            or int(age_text) < min_age_seconds
            or not command.startswith("node ")
            or not vite_executable
            or ".cam-" not in command
            or "apps/client/node_modules/" not in command
            or "--host 127.0.0.1" not in command
            or "--port " not in command
            or "--strictPort" not in command
        ):
            continue
        matches.append(int(pid_text))
    return matches


def cleanup(*, apply: bool) -> int:
    pids = orphaned_vite_servers()
    for pid in pids:
        print(f"{'stopping' if apply else 'would-stop'} orphaned Vite QA server pid={pid}")
        if apply:
            # Only signal processes identified above, not the production nginx,
            # API, CodeLocal host, or a user-owned active QA shell.
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
    if apply and pids:
        time.sleep(1)
    print(f"stale_vite_qa_servers={len(pids)} mode={'apply' if apply else 'dry-run'}")
    return len(pids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cleanup(apply=args.apply)
