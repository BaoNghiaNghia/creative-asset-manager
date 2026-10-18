#!/usr/bin/env python3
"""Conservative post-reboot VPS recovery: audit and repair one obsolete swap entry.

--audit is read-only. --apply-swap is explicitly opt-in, root-only and changes
only a legacy /swapfile entry in /etc/fstab. Network routes, production services,
the active CAM swap, application data and secrets are never modified.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
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
        return result.returncode, output[:2400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, type(exc).__name__


def assess_network_readiness(
    eth0: str,
    ipv4_default_routes: str,
    ipv6_default_routes: str,
    wait_online_state: str,
) -> tuple[str, list[str]]:
    """Classify existing evidence; never infer a safe route or edit the host.

    A failed boot-time wait-online result may be stale. A working IPv4 default
    route does not prove that the off-link IPv6 gateway is correct.
    """
    observations: list[str] = []
    has_ipv4_default = any(
        line.strip().startswith("default ") and " dev eth0" in line
        for line in ipv4_default_routes.splitlines()
    )
    has_ipv6_default = any(
        line.strip().startswith("default ") and " dev eth0" in line
        for line in ipv6_default_routes.splitlines()
    )
    routable = bool(re.search(r"State:\s+routable", eth0))
    configuring = bool(re.search(r"State:\s+routable \(configuring\)", eth0))
    wait_failed = "ActiveState=failed" in wait_online_state or "Result=exit-code" in wait_online_state

    # Report only global IPv6 addresses shown on the interface (not DNS,
    # link-local, MAC, or the provider's purported gateway).
    global_ipv6 = False
    address_lines = eth0.splitlines()
    for index, line in enumerate(address_lines):
        if "Address:" not in line:
            continue
        candidates = [line.split("Address:", 1)[1]]
        for continuation in address_lines[index + 1:]:
            if not continuation.strip() or re.match(r"^\s*[A-Za-z][A-Za-z0-9 _-]*:\s", continuation):
                break
            candidates.append(continuation)
        for candidate in candidates:
            for token in candidate.split():
                try:
                    addr = ipaddress.ip_address(token.split("/")[0])
                except ValueError:
                    continue
                if isinstance(addr, ipaddress.IPv6Address) and addr.is_global:
                    global_ipv6 = True
                    break

    if not has_ipv4_default:
        observations.append("BLOCKER: eth0 has no observed IPv4 default route; verify out-of-band console.")
        return "BLOCKED_NETWORK", observations
    observations.append("PASS: IPv4 default route via eth0 exists.")
    if not routable:
        observations.append("WARN: eth0 is not reported as routable.")
    if configuring:
        observations.append("WARN: eth0 is still configuring; investigate the underlying networkd task.")
    if global_ipv6 and not has_ipv6_default:
        observations.append(
            "WARN: global IPv6 is present, but no IPv6 default route exists. "
            "Confirm gateway and on-link requirements with VPS provider."
        )
    if wait_failed:
        observations.append(
            "WARN: wait-online service failed previously; check after validating "
            "provider network settings, do not just mask/reset the unit."
        )
    if configuring or (global_ipv6 and not has_ipv6_default) or wait_failed or not routable:
        return "DEGRADED_REVIEW_REQUIRED", observations
    return "NETWORK_READY", observations


def network_check() -> int:
    """Read-only triage. Does not read Netplan secrets or mutate systemd."""
    probes = [
        ("eth0", ["networkctl", "status", "eth0", "--no-pager"]),
        ("ipv4", ["ip", "-4", "route", "show", "default"]),
        ("ipv6", ["ip", "-6", "route", "show", "default"]),
        ("wait", ["systemctl", "show", "systemd-networkd-wait-online.service",
                  "-p", "ActiveState", "-p", "Result", "--no-pager"]),
    ]
    results = {name: command_summary(command) for name, command in probes}
    if any(code != 0 for code, _ in results.values()):
        print("NETWORK_AUDIT=INCOMPLETE")
        for name, (code, _) in results.items():
            print(f"PROBE_{name.upper()}_EXIT={code}")
        print("NETWORK_CHANGE_APPLIED=NO")
        return 2
    status, findings = assess_network_readiness(
        results["eth0"][1], results["ipv4"][1], results["ipv6"][1], results["wait"][1],
    )
    print(f"NETWORK_AUDIT={status}")
    for finding in findings:
        print(finding)
    print("NETWORK_CHANGE_APPLIED=NO")
    return 2 if status == "BLOCKED_NETWORK" else 0


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
    group.add_argument("--network-check", action="store_true", help="Read-only network triage")
    args = parser.parse_args()
    if args.audit:
        audit()
        return 0
    if args.network_check:
        return network_check()
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
