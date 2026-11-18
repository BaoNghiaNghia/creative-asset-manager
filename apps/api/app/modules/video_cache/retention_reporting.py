"""Read-only, aggregate video-cache retention observability (no object IDs)."""
from __future__ import annotations

from datetime import timedelta
from sqlalchemy import func, or_, select

from app.modules.video_cache.model import (
    VideoCacheCleanupRunModel, VideoCacheObjectModel, utcnow,
)


def retention_status(session, settings) -> dict[str, object]:
    cutoff = utcnow() - timedelta(days=settings.R2_VIDEO_CACHE_MAX_AGE_DAYS)
    expired_count, expired_bytes = session.execute(select(
        func.count(VideoCacheObjectModel.id),
        func.coalesce(func.sum(
            VideoCacheObjectModel.size_bytes +
            VideoCacheObjectModel.playback_size_bytes
        ), 0),
    ).where(
        VideoCacheObjectModel.status == "ready",
        or_(VideoCacheObjectModel.cached_at.is_(None),
            VideoCacheObjectModel.cached_at <= cutoff),
    )).one()
    latest = session.scalar(select(VideoCacheCleanupRunModel).order_by(
        VideoCacheCleanupRunModel.finished_at.desc()
    ).limit(1))
    since = utcnow() - timedelta(days=1)
    daily = session.execute(select(
        func.coalesce(func.sum(VideoCacheCleanupRunModel.expired_deleted), 0),
        func.coalesce(func.sum(VideoCacheCleanupRunModel.expired_bytes_freed), 0),
        func.coalesce(func.sum(VideoCacheCleanupRunModel.delete_failures), 0),
    ).where(VideoCacheCleanupRunModel.finished_at >= since)).one()
    return {
        "max_age_days": settings.R2_VIDEO_CACHE_MAX_AGE_DAYS,
        "expired_ready_objects": int(expired_count),
        "expired_ready_bytes": int(expired_bytes),
        "last_cleanup_at": latest.finished_at.isoformat() if latest else None,
        "last_cleanup_status": latest.status if latest else "not_run",
        "last_cleanup_deleted": int(latest.expired_deleted) if latest else 0,
        "last_cleanup_bytes_freed": int(latest.expired_bytes_freed) if latest else 0,
        "last_cleanup_delete_failures": int(latest.delete_failures) if latest else 0,
        "last_24h_expired_deleted": int(daily[0]),
        "last_24h_bytes_freed": int(daily[1]),
        "last_24h_delete_failures": int(daily[2]),
    }
