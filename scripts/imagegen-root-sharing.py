"""Run with the existing production_env.py run-redacted operator wrapper."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from fastapi import HTTPException
from app.modules.etsy_spy.imagegen_root_sharing import run_root_sharing


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-root")
    args = parser.parse_args()
    if args.apply and args.confirm_root != "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl":
        parser.error("--apply requires the exact approved --confirm-root")
    try:
        print(json.dumps(asyncio.run(run_root_sharing(apply=args.apply))), flush=True)
    except HTTPException as exc:
        print(json.dumps({"ok": False, "status": exc.status_code, "error": str(exc.detail)}), flush=True)
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
