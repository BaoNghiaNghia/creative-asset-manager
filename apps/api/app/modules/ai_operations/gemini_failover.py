from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_governance.model import AiModelRateLimitStateModel
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository
from app.modules.processing.model import ProcessingJobModel

DEFERRED_CODES = (
    "video_gemini_quota_deferred", "video_gemini_rate_limited",
    "gemini_quota_deferred", "gemini_image_quota_deferred",
    "ai_model_rate_limited", "ai_provider_rate_limited",
)


def backup_is_active(session: Session, settings: Settings, tenant_id: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    threshold = max(1, int(getattr(settings, "GEMINI_FAILOVER_DEFERRED_THRESHOLD", 100)))
    count = session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.status.in_(("pending", "retry")),
        ProcessingJobModel.last_error_code.in_(DEFERRED_CODES),
    )) or 0
    return int(count) >= threshold


def _backup_is_configured(session: Session, tenant_id: str) -> bool:
    repo = CreativeAiCredentialRepository(session, None)
    return bool(repo.list_active_backup_providers(tenant_id))


def rate_limit_provider_key(
    session: Session, settings: Settings, tenant_id: str, provider: str, *,
    model: str | None = None, rpm: int | None = None,
    minimum_interval_seconds: float | None = None, now: datetime | None = None,
) -> str:
    """Select primary/backup by availability, keeping primary as tie-breaker."""
    if provider != "gemini" or not _backup_is_configured(session, tenant_id):
        return provider
    if not model or not rpm or not minimum_interval_seconds:
        return repo.list_active_backup_providers(tenant_id)[0] if backup_is_active(session, settings, tenant_id, now) else provider
    from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository
    limiter = AiModelRateLimitRepository(session)
    repository = CreativeAiCredentialRepository(session, None)
    candidates = ("gemini",) + repository.list_active_backup_providers(tenant_id)
    decisions = {
        candidate: limiter.next_start(
            tenant_id=tenant_id, provider=candidate, model=model, rpm=rpm,
            minimum_interval_seconds=minimum_interval_seconds, now=now,
        )
        for candidate in candidates
    }
    states = {
        state.provider: state
        for state in session.scalars(
            select(AiModelRateLimitStateModel).where(
                AiModelRateLimitStateModel.tenant_id == tenant_id,
                AiModelRateLimitStateModel.model == model,
                AiModelRateLimitStateModel.provider.in_(candidates),
            )
        )
    }

    def rank(candidate: str) -> tuple[bool, datetime, bool]:
        state = states.get(candidate)
        last_started = (
            state.last_started_at
            if state is not None and state.last_started_at is not None
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        if last_started.tzinfo is None or last_started.utcoffset() is None:
            last_started = last_started.replace(tzinfo=timezone.utc)
        # Prefer an available key first. If both are available, choose the key
        # that has gone longest without a start so primary/backup alternate.
        return (
            not decisions[candidate].allowed,
            last_started,
            candidate != "gemini",
        )

    return min(candidates, key=rank)
