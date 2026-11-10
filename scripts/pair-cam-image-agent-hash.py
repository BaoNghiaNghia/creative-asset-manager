#!/usr/bin/env python3
"""Register an Image Agent using only a Windows-generated token SHA256 hash.

Run on the CAM production host with its private environment file available.
The plaintext bearer token MUST NOT leave the Windows workstation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path("/etc/creative-asset-manager/production.env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--token-sha256", required=True)
    parser.add_argument("--label", default="DESKTOP-3FR0LF0")
    parser.add_argument("--tenant-id", choices=("boostgame", "creative-assets-prod"),
                        required=True)
    args = parser.parse_args()
    if os.name != "posix" or not ENV_FILE.is_file():
        raise SystemExit("Production host and protected environment file required")
    load_dotenv(ENV_FILE, override=False)
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("Refusing to register against a non-production database")
    from app.core.database import SessionLocal
    from app.modules.etsy_spy.image_agent_pairing import register_hashed_image_agent
    with SessionLocal() as db:
        state = register_hashed_image_agent(
            db, agent_id=args.agent_id, token_sha256=args.token_sha256,
            label=args.label, tenant_id=args.tenant_id)
    print(json.dumps({"status": state, "agent_id": args.agent_id,
                      "tenant_id": args.tenant_id, "secret_transferred": False}))


if __name__ == "__main__":
    main()
