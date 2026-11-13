#!/usr/bin/env python3
"""Fail-closed local CAM API liveness watchdog.

Only the web API is restarted after two failed loopback readiness checks.
Workers, PostgreSQL, and user sessions are never modified by this script.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UNIT = "creative-asset-manager-api.service"
READY_URL = "http://127.0.0.1:8000/ready"
READY_HOST = "creative-assets.ddns.net"
STATE_DIR = Path("/var/lib/cam-api-watchdog")
GRACE_SECONDS = 120
COOLDOWN_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 3
PROBE_GAP_SECONDS = 2


def output(*args: str, timeout: float = 5) -> str:
    result = subprocess.run(
        list(args), capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode:
        return ""
    return result.stdout.strip()


def service_pid() -> int:
    raw = output("systemctl", "show", "--property=MainPID", "--value", UNIT)
    try:
        return int(raw)
    except ValueError:
        return 0


def service_active() -> bool:
    return (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", UNIT],
            check=False,
            capture_output=True,
            timeout=5,
        ).returncode == 0
    )


def service_age_seconds(pid: int) -> int:
    try:
        return int(output("ps", "-o", "etimes=", "-p", str(pid)) or "0")
    except ValueError:
        return 0


def ready() -> bool:
    req = Request(
        READY_URL,
        headers={"Host": READY_HOST, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as response:
            return response.status == 200
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def eligible(active: bool, pid: int, age_seconds: int, seconds_since_restart: float) -> bool:
    return bool(
        active
        and pid > 1
        and age_seconds >= GRACE_SECONDS
        and seconds_since_restart >= COOLDOWN_SECONDS
    )


def main() -> int:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE_DIR / "guard.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        stamp = STATE_DIR / "last-restart-epoch"
        try:
            last_restart = float(stamp.read_text().strip())
        except (OSError, ValueError):
            last_restart = 0.0
        pid = service_pid()
        age = service_age_seconds(pid) if pid else 0
        if not eligible(service_active(), pid, age, time.time() - last_restart):
            return 0
        if ready():
            return 0
        time.sleep(PROBE_GAP_SECONDS)
        if ready():
            return 0

        # A deploy can replace the process between checks. Never restart the
        # replacement or a service that systemd is already stopping.
        if not eligible(
            service_active(), pid, service_age_seconds(pid), time.time() - last_restart
        ) or service_pid() != pid:
            return 0

        print(f"CAM API liveness failure: two 3s readiness probes failed for PID {pid}; scheduling API-only restart.", flush=True)
        result = subprocess.run(
            ["systemctl", "--no-block", "restart", UNIT],
            check=False, capture_output=True, timeout=8,
        )
        if result.returncode:
            print(f"CAM API liveness restart request failed ({result.returncode}).", flush=True)
            return 1
        temporary = STATE_DIR / ("stamp." + str(os.getpid()))
        temporary.write_text(str(time.time()))
        os.replace(temporary, stamp)
        print("CAM API-only restart requested; cooldown active for 900 seconds.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
