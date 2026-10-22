"""Fail-closed production preflight for data-changing retention jobs."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from app.modules.database_backup.verified_receipt import has_recent_verified_receipt


class RetentionProductionGuardError(RuntimeError):
    """Non-sensitive reason code; no file paths or database data in messages."""


def retention_block_reason(
    settings: Any, *, now: datetime | None = None, available_bytes: int | None = None,
) -> str | None:
    """Return a stable reason code, or None if it is safe to begin retention.

    The receipt proves a recently remote-verified backup, not a tested restore.
    Operators must separately verify restore readiness before enabling the flag.
    """
    if not bool(getattr(settings, "is_production", False)):
        return None
    minimum = int(getattr(settings, "RETENTION_CLEANUP_MIN_FREE_BYTES", 8 * 1024**3))
    if available_bytes is None:
        try:
            stats = os.statvfs("/")
            available_bytes = stats.f_bavail * stats.f_frsize
        except OSError:
            return "disk_space_unavailable"
    if available_bytes < minimum:
        return "insufficient_disk_headroom"
    if not bool(getattr(settings, "DATABASE_BACKUP_ENABLED", False)):
        return "backup_feature_disabled"
    if not has_recent_verified_receipt(
        str(getattr(settings, "DATABASE_BACKUP_STAGING_DIRECTORY", "")),
        now=now,
        max_age_hours=int(getattr(settings, "RETENTION_CLEANUP_BACKUP_MAX_AGE_HOURS", 108)),
    ):
        return "remote_verified_backup_receipt_missing_or_stale"
    return None


def enforce_retention_guard(settings: Any) -> None:
    reason = retention_block_reason(settings)
    if reason is not None:
        raise RetentionProductionGuardError(reason)
