"""Bounded, audit-only-by-default 30-day R2 video cache cleanup.

Only exact DB-ledger video-cache/{tenant}/{hash}/original and playback keys
may be deleted. Never lists a bucket or deletes source media / Etsy images.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Physically delete expired cache copies")
    parser.add_argument("--max-items", type=int, default=250)
    parser.add_argument("--env-file", type=Path,
                        default=Path("/etc/creative-asset-manager/production.env"))
    args = parser.parse_args()
    if not 1 <= args.max_items <= 250:
        parser.error("--max-items must be 1..250")
    # Must load the exact production settings *before* the database engine is imported.
    tools_dir = Path(__file__).resolve().parents[4] / "deploy/tools"
    sys.path.insert(0, str(tools_dir))
    from production_env import parse_environment_file
    import os
    os.environ.update(parse_environment_file(args.env_file))
    from app.core.config import Settings
    settings = Settings(_env_file=None)
    if not settings.R2_VIDEO_CACHE_ENABLED or settings.R2_BUCKET_NAME != "creative-asset-video-cache":
        print('{"status":"blocked","reason":"video_cache_not_ready"}')
        return 2
    if settings.R2_VIDEO_CACHE_MAX_AGE_DAYS != 30:
        print('{"status":"blocked","reason":"age_policy_not_30_days"}')
        return 2
    # Imported only after env-aware settings have been applied.
    from app.core.database import SessionLocal
    from app.modules.video_cache.cleanup import VideoCacheCleanup
    from app.modules.video_cache.model import utcnow
    from app.modules.video_cache.retention_reporting import retention_status
    from app.providers.cloudflare.r2 import R2Adapter
    with SessionLocal() as session:
        before = retention_status(session, settings)
    result = {
        "max_age_days": 30,
        "mode": "apply" if args.apply else "dry_run",
        "expired_ready_before": before["expired_ready_objects"],
        "expired_bytes_before": before["expired_ready_bytes"],
        "expired_deleted": 0,
        "expired_bytes_freed": 0,
        "delete_failures": 0,
    }
    if not args.apply:
        print(json.dumps(result, sort_keys=True))
        return 0

    cleanup = VideoCacheCleanup(SessionLocal, settings, R2Adapter(settings))
    started = utcnow()
    for _ in range(args.max_items):
        claim = cleanup._claim_expired()
        if claim is None:
            break
        tenant, record_id, owner, byte_count = claim
        done = asyncio.run(cleanup.quota.delete_claimed(tenant, record_id, owner))
        if done:
            result["expired_deleted"] += 1
            result["expired_bytes_freed"] += byte_count
        else:
            result["delete_failures"] += 1
    stats = {
        "recovered": 0, "deleted": result["expired_deleted"], "evicted": 0,
        "expired_deleted": result["expired_deleted"],
        "expired_bytes_freed": result["expired_bytes_freed"],
        "delete_failures": result["delete_failures"],
    }
    cleanup._record_cleanup_run(started, stats)
    with SessionLocal() as session:
        after = retention_status(session, settings)
    result["expired_ready_after"] = after["expired_ready_objects"]
    result["expired_bytes_after"] = after["expired_ready_bytes"]
    print(json.dumps(result, sort_keys=True))
    return 0 if result["delete_failures"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
