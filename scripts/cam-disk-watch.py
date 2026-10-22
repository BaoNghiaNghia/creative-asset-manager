#!/usr/bin/env python3
"""Lightweight read-only root-filesystem pressure monitor (no disk traversal).

Designed for 15-minute systemd monitoring. The process never deletes files,
opens application databases, follows user paths or writes a local cache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def classify(percentage: float) -> str:
    if percentage >= 95.0:
        return "emergency"
    if percentage >= 90.0:
        return "critical"
    if percentage >= 85.0:
        return "warning"
    if percentage >= 80.0:
        return "notice"
    return "normal"


def measure(root: str = "/") -> dict[str, object]:
    stats = os.statvfs(root)
    block = stats.f_frsize
    used = (stats.f_blocks - stats.f_bfree) * block
    available = stats.f_bavail * block
    denominator = used + available
    if denominator <= 0:
        raise RuntimeError("Disk size is invalid")
    percentage = round(100.0 * used / denominator, 2)
    return {
        "event": "cam_disk_pressure",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "mount": root,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": percentage,
        "level": classify(percentage),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount", default="/", help="Mounted filesystem to inspect")
    args = parser.parse_args(argv)
    try:
        report = measure(args.mount)
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"event": "cam_disk_pressure_error", "error_type": type(exc).__name__}),
              file=sys.stderr)
        return 2
    sink = sys.stderr if report["level"] in {"critical", "emergency"} else sys.stdout
    print(json.dumps(report, sort_keys=True), file=sink)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
