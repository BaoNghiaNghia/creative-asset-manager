from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.model import ProcessingJobModel

DEFERRED_CODES = ("video_gemini_quota_deferred", "video_gemini_rate_limited", "gemini_quota_deferred", "gemini_image_quota_deferred", "ai_model_rate_limited", "ai_provider_rate_limited")

def backup_is_active(session: Session, settings: Settings, tenant_id: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    threshold = max(1, int(settings.GEMINI_FAILOVER_DEFERRED_THRESHOLD))
    count = session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.status.in_(("pending", "retry")),
        ProcessingJobModel.last_error_code.in_(DEFERRED_CODES),
        ProcessingJobModel.next_attempt_at > now,
    )) or 0
    return int(count) >= threshold

def rate_limit_provider_key(session: Session, settings: Settings, tenant_id: str, provider: str) -> str:
    return "gemini_backup" if provider == "gemini" and backup_is_active(session, settings, tenant_id) else provider
