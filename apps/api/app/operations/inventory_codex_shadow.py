"""Operator-only pilot of Inventory Codex CLI. No Sheets or database write access.

preflight: read-only local login / non-root identity check.
shadow: accepts a bounded, already-authorized cell evidence JSON file and
returns review_required findings; never attempts workbook writeback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from app.core.config import Settings
from app.modules.realistic_review_ugc.codex_account_manager import account_home
from app.modules.inventory.ai.codex_cli_shadow import (
    InventoryCodexUnavailable,
    MAX_INPUT_BYTES,
    review_inventory_evidence,
)


# Reuse the existing, separately authenticated worker profiles. No copying
# auth.json, root HOME, or generating additional API credentials.
DEFAULT_BINARY = "/usr/bin/codex"
DEFAULT_ACCOUNT = "account-1"


def existing_profile(account: str, settings: Settings) -> Path:
    if account not in {"account-1", "account-2"}:
        raise ValueError("inventory_codex_unknown_account")
    return account_home(account, settings.CODEX_IMAGE_HOME)



def _env(home: Path) -> dict[str, str]:
    return {
        "CODEX_HOME": str(home),
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
    }


def preflight(home: Path, binary: Path) -> dict[str, object]:
    if (os.geteuid() == 0 or not home.is_absolute() or home.is_symlink() or
            not home.is_dir() or home.stat().st_uid != os.geteuid()):
        return {"engine": "codex_cli", "mode": "shadow", "ready": False,
                "reason": "dedicated_service_identity_unavailable"}
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        return {"engine": "codex_cli", "mode": "shadow", "ready": False,
                "reason": "codex_binary_unavailable"}
    try:
        run = subprocess.run(
            [str(binary), "login", "status"], cwd=home, env=_env(home),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"engine": "codex_cli", "mode": "shadow", "ready": False,
                "reason": "codex_login_check_unavailable"}
    # Never echo identity/email/tokens, only a safe boolean.
    connected = run.returncode == 0 and b"Logged in" in run.stdout
    return {"engine": "codex_cli", "mode": "shadow", "ready": connected,
            "reason": "ready" if connected else "codex_service_login_required"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "smoke", "shadow"))
    parser.add_argument("--account", choices=("account-1", "account-2"),
                        default=DEFAULT_ACCOUNT,
                        help="Existing managed Codex profile; no automatic account rotation")
    parser.add_argument("--binary", type=Path, default=None)
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    home = existing_profile(args.account, settings)
    configured_binary = settings.CODEX_IMAGE_BINARY or DEFAULT_BINARY
    executable = args.binary or Path(shutil.which(configured_binary) or configured_binary)
    state = {**preflight(home, executable), "account": args.account}
    if args.action == "preflight":
        print(json.dumps(state, sort_keys=True))
        return 0 if state["ready"] else 2
    if not state["ready"]:
        print(json.dumps(state, sort_keys=True))
        return 2
    if args.action == "smoke":
        # Deliberately synthetic, no inventory/tenant/customer information.
        demo_hash = hashlib.sha256(b"cam-inventory-synthetic-smoke").hexdigest()
        data = {
            "business_date": "2026-11-18",
            "cells": [{
                "sheet": "SYNTHETIC_NO_REAL_DATA",
                "cell": "A1",
                "raw_value": "10 test units; source unit unknown",
                "evidence_hash": demo_hash,
            }],
        }
    else:
        if os.environ.get("INVENTORY_CODEX_SHADOW_ENABLED", "").lower() not in {"1", "true"}:
            print('{"engine":"codex_cli","mode":"shadow","ready":false,"reason":"shadow_gate_disabled"}')
            return 2
        p = args.evidence_file
        if (p is None or not p.is_absolute() or p.is_symlink() or not p.is_file() or
                p.stat().st_size > MAX_INPUT_BYTES or p.stat().st_size < 2):
            print('{"engine":"codex_cli","mode":"shadow","ready":false,"reason":"invalid_evidence_file"}')
            return 2
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, OSError):
            print('{"engine":"codex_cli","mode":"shadow","ready":false,"reason":"invalid_evidence_file"}')
            return 2
    try:
        result = review_inventory_evidence(
            data, codex_home=str(home), binary=str(executable),
            model=args.model,
        )
    except (ValueError, UnicodeError, OSError, InventoryCodexUnavailable):
        print('{"engine":"codex_cli","mode":"shadow","ready":false,"reason":"review_unavailable"}')
        return 2
    if args.action == "smoke":
        print(json.dumps({
            "engine": "codex_cli",
            "account": args.account,
            "mode": "shadow",
            "scenario": "synthetic_evidence",
            "ready": True,
            "validated_review": True,
            "writes": 0,
        }, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
