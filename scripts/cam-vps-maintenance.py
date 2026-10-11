#!/usr/bin/env python3
"""Root-installed allowlisted VPS maintenance actions; no arbitrary commands.

Install as /usr/local/sbin/cam-vps-maintenance, owned by root with no write
access for the CodeLocal runtime. Never run a copy from a writable checkout.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

FSTAB = Path("/etc/fstab")
STATE_DIR = Path("/var/lib/creative-asset-manager/maintenance")
LOG_DIR = Path("/var/log/cam-vps-maintenance")
AUDIT_LOG = LOG_DIR / "audit.jsonl"
LOCK = STATE_DIR / "maintenance.lock"
BACKUP = STATE_DIR / "fstab-before-swap-repair"
MARKER = STATE_DIR / "fstab-repair.json"
OLD_SWAP = "/swapfile"
LIVE_SWAP = "/swapfile-camqa"

ALLOWED = (
    "status",
    "swap-repair",
    "swap-rollback",
    "network-audit",
    "stage1-retention-retry",
)


def checked_run(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """No shell, no caller-supplied command, no inherited provider credentials."""
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=timeout)


def legacy_swap_indices(text: str) -> list[int]:
    result = []
    for idx, line in enumerate(text.splitlines(keepends=True)):
        if line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 4 and fields[0] == OLD_SWAP and fields[2] == "swap":
            result.append(idx)
    return result


def swap_entries(text: str) -> list[str]:
    return [
        line.split()[0] for line in text.splitlines()
        if line.split() and not line.lstrip().startswith("#")
        and len(line.split()) >= 3 and line.split()[2] == "swap"
    ]


def require_fstab_file(path: Path) -> os.stat_result:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("fstab path must be an ordinary non-symlink file")
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode):
        raise RuntimeError("fstab is not a regular file")
    return meta


def write_atomic(path: Path, contents: str, meta: os.stat_result) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".cam-fstab-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), meta.st_uid, meta.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(meta.st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def expected_repair(text: str) -> str:
    lines = text.splitlines(keepends=True)
    indices = legacy_swap_indices(text)
    if len(indices) != 1:
        raise RuntimeError("Requires exactly one active legacy swap entry")
    lines[indices[0]] = "# CAM: retired invalid legacy swap; " + lines[indices[0]]
    return "".join(lines)


def live_swap_verified(text: str, active_names: set[str]) -> bool:
    return LIVE_SWAP in active_names and LIVE_SWAP in swap_entries(text)


def do_swap_repair(*, fstab: Path, backup: Path, marker: Path,
                   active_names: set[str]) -> str:
    meta = require_fstab_file(fstab)
    original = fstab.read_text(encoding="utf-8")
    if not live_swap_verified(original, active_names):
        raise RuntimeError("Active CAM swap must be enabled and persisted in fstab")
    indices = legacy_swap_indices(original)
    if not indices:
        return "No obsolete active swap entry; nothing changed"
    if len(indices) != 1:
        raise RuntimeError("More than one obsolete entry; refusing ambiguous edit")
    if backup.exists() or backup.is_symlink() or marker.exists() or marker.is_symlink():
        raise RuntimeError("Existing repair backup/marker must be reviewed before another edit")
    repaired = expected_repair(original)
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(original.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"original_sha256": digest, "action": "swap-repair"}))
        handle.flush()
        os.fsync(handle.fileno())
    write_atomic(fstab, repaired, meta)
    return "Obsolete swap entry retired; backup and rollback marker preserved"


def do_swap_rollback(*, fstab: Path, backup: Path, marker: Path,
                     active_names: set[str]) -> str:
    meta = require_fstab_file(fstab)
    if backup.is_symlink() or marker.is_symlink() or not backup.is_file() or not marker.is_file():
        raise RuntimeError("Missing protected backup or rollback marker")
    saved = backup.read_bytes()
    details = json.loads(marker.read_text(encoding="utf-8"))
    if (details.get("action") != "swap-repair" or
            details.get("original_sha256") != hashlib.sha256(saved).hexdigest()):
        raise RuntimeError("Backup integrity validation failed")
    original = saved.decode("utf-8")
    current = fstab.read_text(encoding="utf-8")
    if not live_swap_verified(current, active_names):
        raise RuntimeError("Active CAM swap not verified; refusing rollback")
    if current != expected_repair(original):
        raise RuntimeError("fstab changed since repair; refusing to overwrite operator changes")
    write_atomic(fstab, original, meta)
    return "Previous fstab restored exactly; obsolete swap warning may return"


def run_status() -> str:
    checks = [
        ["uname", "-r"],
        ["swapon", "--show"],
        ["findmnt", "--fstab", "--types", "swap"],
        ["systemctl", "--failed", "--no-pager"],
        ["pg_isready", "-h", "127.0.0.1", "-p", "5432"],
        ["df", "-h", "/"],
    ]
    lines = []
    for argv in checks:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        lines.append(f"{argv[0]} exit={proc.returncode}: {(proc.stdout or proc.stderr).strip()[:1400]}")
    return "\n".join(lines)


def run_network_audit() -> str:
    checks = [
        ["networkctl", "list", "--no-pager"],
        ["networkctl", "status", "eth0", "--no-pager"],
        ["ip", "-4", "route", "show", "default"],
        ["ip", "-6", "route", "show", "default"],
    ]
    lines = []
    for argv in checks:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        lines.append(f"{argv[0]} exit={proc.returncode}: {(proc.stdout or proc.stderr).strip()[:2200]}")
    return "\n".join(lines)


def perform(action: str, *, active_names: set[str] | None = None,
            fstab: Path = FSTAB, state_dir: Path = STATE_DIR) -> str:
    if action == "status":
        return run_status()
    if action == "network-audit":
        return run_network_audit()
    if action == "stage1-retention-retry":
        checked_run(["systemctl", "start", "creative-asset-manager-stage1-temp-retention.service"],
                    timeout=180)
        return "Stage 1 retention run completed (check systemd journal for counts)"
    if action not in {"swap-repair", "swap-rollback"}:
        raise ValueError("Action not allowlisted")
    if active_names is None:
        swaps = checked_run(["swapon", "--show=NAME", "--noheadings", "--raw"])
        active_names = set(swaps.stdout.split())
    backup = state_dir / "fstab-before-swap-repair"
    marker = state_dir / "fstab-repair.json"
    if action == "swap-repair":
        result = do_swap_repair(fstab=fstab, backup=backup, marker=marker, active_names=active_names)
    else:
        result = do_swap_rollback(fstab=fstab, backup=backup, marker=marker, active_names=active_names)
    checked_run(["systemctl", "daemon-reload"])
    if action == "swap-repair":
        checked_run(["systemctl", "reset-failed", "swapfile.swap"])
    return result


def safe_dir(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked maintenance directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != 0:
        raise RuntimeError("Maintenance directory is not root-owned")
    os.chmod(path, 0o700)


def audit(action: str, *, result: str, error: str | None = None) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "status": result,
        "caller_uid": os.environ.get("SUDO_UID", str(os.getuid())),
        "error_type": error,
    }
    fd = os.open(AUDIT_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ALLOWED)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("root execution required through a reviewed maintenance policy")
    os.umask(0o077)
    safe_dir(STATE_DIR)
    safe_dir(LOG_DIR)
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w"):
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            output = perform(args.action)
            audit(args.action, result="success")
            print(output)
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            audit(args.action, result="failure", error=type(exc).__name__)
            print(f"MAINTENANCE FAILED ({type(exc).__name__}); see root-owned audit log", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
