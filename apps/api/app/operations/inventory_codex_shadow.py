"""Operator-only pilot of Inventory Codex CLI. No Sheets or database write access.

preflight: read-only local login / non-root identity check.
shadow: accepts a bounded, already-authorized cell evidence JSON file and
returns review_required findings; never attempts workbook writeback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from app.modules.inventory.ai.codex_cli_shadow import (
    InventoryCodexUnavailable,
    MAX_INPUT_BYTES,
    review_inventory_evidence,
)


DEFAULT_HOME = Path("/var/lib/creative-asset-manager/inventory-codex")
DEFAULT_BINARY = "/usr/bin/codex"


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
    parser.add_argument("action", choices=("preflight", "shadow"))
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--binary", type=Path, default=Path(DEFAULT_BINARY))
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    state = preflight(args.codex_home, args.binary)
    if args.action == "preflight":
        print(json.dumps(state, sort_keys=True))
        return 0 if state["ready"] else 2
    if not state["ready"]:
        print(json.dumps(state, sort_keys=True))
        return 2
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
        result = review_inventory_evidence(
            data, codex_home=str(args.codex_home), binary=str(args.binary),
            model=args.model,
        )
    except (ValueError, UnicodeError, OSError, InventoryCodexUnavailable):
        print('{"engine":"codex_cli","mode":"shadow","ready":false,"reason":"review_unavailable"}')
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
