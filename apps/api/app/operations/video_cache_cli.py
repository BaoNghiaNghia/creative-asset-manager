"""Read-only, credential-free operator status for the R2 video cache."""
from __future__ import annotations
import argparse
import json
from sqlalchemy import func, select
from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.quota import VideoCacheQuota


def video_cache_status(session, settings):
    usage = VideoCacheQuota.usage(session)
    counts = dict(session.execute(select(
        VideoCacheObjectModel.status, func.count(VideoCacheObjectModel.id)
    ).group_by(VideoCacheObjectModel.status)).all())
    hard = settings.R2_VIDEO_CACHE_HARD_LIMIT_BYTES
    return {
        "feature_enabled": settings.R2_VIDEO_CACHE_ENABLED,
        "ready_bytes": usage.ready_bytes,
        "reserved_bytes": usage.reserved_bytes,
        "deleting_bytes": usage.deleting_bytes,
        "effective_bytes": usage.effective_bytes,
        "ready_objects": int(counts.get("ready", 0)),
        "preparing_objects": int(counts.get("preparing", 0)),
        "retry_objects": int(counts.get("retry", 0)),
        "deleting_objects": int(counts.get("deleting", 0)),
        "soft_limit": settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES,
        "hard_limit": hard,
        "pressure_percent": round(100 * usage.effective_bytes / hard, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only R2 video cache status")
    parser.parse_args()
    settings = Settings()
    with SessionLocal() as session:
        print(json.dumps(video_cache_status(session, settings), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
