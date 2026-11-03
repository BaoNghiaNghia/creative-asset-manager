#!/usr/bin/env python3
"""Read-only G3 production pilot gate preflight.

Never executes an AI provider, creates a job, changes a flag or prints secrets.
This is an operator diagnostic, NOT sign-off for migration/backup or image rights.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "deploy" / "tools"
sys.path.insert(0, str(TOOLS))
from production_env import (  # noqa: E402
    SafeConfigurationError, parse_environment_file, validate_file_security,
)

PILOT_FLAGS = (
    "TYPOGRAPHY_V2_ENABLED",
    "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED",
    "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED",
)
MANUAL_GATES = (
    "db_migration_and_restore_test",
    "capacity_and_cost_budget_approval",
    "tenant_rbac_review",
    "eight_rights_verified_references",
    "authenticated_browser_visual_qa",
)


def enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def readiness_report(
    values: dict[str, str],
    *,
    tenant_id: str = "",
    release_root: Path | None = None,
    disk_free_mib: int | None = None,
    mem_available_mib: int | None = None,
) -> dict:
    """No DB queries, provider initialization or state changes are performed."""
    flags = {name: enabled(values.get(name)) for name in PILOT_FLAGS}
    canary = {item.strip() for item in values.get("TYPOGRAPHY_V2_CANARY_TENANT_IDS", "").split(",") if item.strip()}
    checks = {
        "typography_feature_enabled": flags["TYPOGRAPHY_V2_ENABLED"],
        "explicit_canary_present": bool(canary),
        "target_tenant_allowlisted": bool(tenant_id and tenant_id in canary),
        "pilot_worker_enabled": flags["TYPOGRAPHY_V2_PILOT_WORKER_ENABLED"],
        "rollback_signoff_flag": flags["TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED"],
        "disk_headroom_at_least_2gib": disk_free_mib is not None and disk_free_mib >= 2048,
        "available_memory_at_least_3gib": mem_available_mib is not None and mem_available_mib >= 3072,
    }
    if release_root is not None:
        current = release_root / "current"
        previous = release_root / "previous"
        try:
            current_target = current.resolve(strict=True) if current.is_symlink() else None
            previous_target = previous.resolve(strict=True) if previous.is_symlink() else None
            releases = (release_root / "releases").resolve(strict=True)
            checks["independent_backend_rollback_release"] = bool(
                current_target and previous_target and current_target != previous_target
                and current_target.parent == releases and previous_target.parent == releases
                and (current_target / "apps/api/.venv/bin/python").is_file()
                and (previous_target / "apps/api/.venv/bin/python").is_file()
            )
        except (OSError, RuntimeError):
            checks["independent_backend_rollback_release"] = False
    else:
        checks["independent_backend_rollback_release"] = False

    return {
        "status": "blocked" if not all(checks.values()) else "pending_manual_signoff",
        "checks": checks,
        "blocking_checks": [key for key, ok in checks.items() if not ok],
        "manual_evidence_not_verified": list(MANUAL_GATES),
        "provider_calls": 0,
        "writes": 0,
        "note": "Do not turn on pilot flags until every manual gate is independently signed off.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Print redacted, read-only G3 pilot preflight JSON.")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--release-root", default="/opt/creative-asset-manager")
    parser.add_argument("--tenant-id", default="", help="Optional known pilot tenant; never printed.")
    args = parser.parse_args()
    try:
        path = Path(args.env_file)
        validate_file_security(path, expected_owner_uid=0)
        values = parse_environment_file(path)
        disk = os.statvfs(args.release_root)
        disk_free = disk.f_bavail * disk.f_frsize // (1024 * 1024)
        with open("/proc/meminfo", encoding="ascii") as source:
            memory = next(
                (int(parts[1]) // 1024 for line in source
                 if (parts := line.split()) and parts[0] == "MemAvailable:"),
                None,
            )
        result = readiness_report(
            values, tenant_id=args.tenant_id,
            release_root=Path(args.release_root),
            disk_free_mib=disk_free, mem_available_mib=memory,
        )
    except (SafeConfigurationError, OSError, ValueError) as exc:
        # Never stringify an exception containing environment values or file contents.
        print(json.dumps({"status": "preflight_unavailable", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
