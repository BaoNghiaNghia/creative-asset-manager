from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.ai_governance.model import AiModelRateLimitStateModel
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository
from app.modules.processing.model import ProcessingJobModel

DEFERRED_CODES = (
    "video_gemini_quota_deferred", "video_gemini_rate_limited",
    "gemini_quota_deferred", "gemini_image_quota_deferred",
    "gemini_model_pool_temporarily_unavailable",
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


def _credential_fingerprints(
    repository: CreativeAiCredentialRepository,
    settings: Settings,
    tenant_id: str,
    candidates: tuple[str, ...],
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    primary = repository.get_metadata(tenant_id, provider="gemini")
    if primary is not None and primary.status == "active":
        fingerprints["gemini"] = primary.secret_fingerprint
    else:
        fallback = (settings.GEMINI_API_KEY or "").strip()
        if fallback:
            fingerprints["gemini"] = hashlib.sha256(fallback.encode()).hexdigest()

    active_backups = {
        item.provider: item
        for item in repository.list_backup_metadata(tenant_id)
        if item.status == "active"
    }
    for candidate in candidates:
        metadata = active_backups.get(candidate)
        if metadata is not None:
            fingerprints[candidate] = metadata.secret_fingerprint
    return fingerprints


def rate_limit_provider_key(
    session: Session, settings: Settings, tenant_id: str, provider: str, *,
    model: str | None = None, rpm: int | None = None,
    minimum_interval_seconds: float | None = None, now: datetime | None = None,
) -> str:
    """Select primary/backup by availability, keeping primary as tie-breaker."""
    if provider != "gemini" or not _backup_is_configured(session, tenant_id):
        return provider
    repository = CreativeAiCredentialRepository(session, None)
    if not model or not rpm or not minimum_interval_seconds:
        return (
            repository.list_active_backup_providers(tenant_id)[0]
            if backup_is_active(session, settings, tenant_id, now)
            else provider
        )
    from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository
    limiter = AiModelRateLimitRepository(session)
    candidates = ("gemini",) + repository.list_active_backup_providers(tenant_id)
    fingerprints = _credential_fingerprints(
        repository,
        settings,
        tenant_id,
        candidates,
    )
    decisions = {
        candidate: limiter.next_start(
            tenant_id=tenant_id, provider=candidate, model=model, rpm=rpm,
            minimum_interval_seconds=minimum_interval_seconds, now=now,
        )
        for candidate in candidates
    }
    model_limit = settings.gemini_model_limits.get(model)
    quota_repository = GeminiProjectQuotaRepository(session)
    quota_decisions = {}
    if model_limit is not None:
        for candidate in candidates:
            fingerprint = fingerprints.get(candidate)
            if not fingerprint:
                continue
            quota_decisions[candidate] = quota_repository.check_request_availability(
                quota_scope=(
                    f"{settings.GEMINI_PROJECT_QUOTA_SCOPE}:"
                    f"{tenant_id}:{fingerprint}"
                ),
                model=model,
                rpd=model_limit.rpd,
                project_rpd=settings.gemini_project_daily_request_limit,
                now=now,
            )
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

    def rank(candidate: str) -> tuple[bool, bool, datetime, bool]:
        state = states.get(candidate)
        last_started = (
            state.last_started_at
            if state is not None and state.last_started_at is not None
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        if last_started.tzinfo is None or last_started.utcoffset() is None:
            last_started = last_started.replace(tzinfo=timezone.utc)
        quota_decision = quota_decisions.get(candidate)
        # Never prefer a credential whose durable Gemini project/model quota
        # is already exhausted. Among credentials with capacity, retain the
        # existing start-gate and least-recently-used ordering.
        return (
            quota_decision is not None and not quota_decision.allowed,
            not decisions[candidate].allowed,
            last_started,
            candidate != "gemini",
        )

    return min(candidates, key=rank)
