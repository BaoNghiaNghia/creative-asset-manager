"""Manual interactive Dola login launcher. Never accepts provider credentials."""
from __future__ import annotations
import argparse
import asyncio
import sys
import time
import config
from browser_pool import BrowserPool
from interactive_login import InteractiveLoginManager, InteractiveLoginState

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Open a private headed browser for manual Dola login.")
    parser.add_argument("account")
    parser.add_argument("--email", default="", help="Optional account metadata only")
    return parser.parse_args(argv)

async def main(argv=None):
    args = parse_args(argv)
    pool = BrowserPool(config.PROFILE_DIR, config.POOL_DB_PATH, config.MAX_CONCURRENCY)
    if args.account not in pool.accounts:
        from pathlib import Path
        profile = Path(config.PROFILE_DIR) / args.account
        profile.mkdir(parents=True, exist_ok=True, mode=0o750)
        profile.chmod(0o750)
        pool._ensure_meta(args.account)
    if args.email: pool.set_email(args.account, args.email)
    manager = InteractiveLoginManager(pool)
    await manager.start(args.account)
    print("Manual browser login started. Use the approved private viewer; no credentials are accepted by this command.", flush=True)
    while True:
        state = manager.status(args.account)["state"]
        if state in {InteractiveLoginState.SUCCESS, InteractiveLoginState.FAILED, InteractiveLoginState.CANCELLED, InteractiveLoginState.EXPIRED}:
            return 0 if state == InteractiveLoginState.SUCCESS else 1
        await asyncio.sleep(1)

if __name__ == "__main__":
    try: sys.exit(asyncio.run(main()))
    except KeyboardInterrupt: sys.exit(130)
