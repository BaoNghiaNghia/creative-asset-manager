from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.processing.handlers import ClaimedJob, DeferredJobOutcome
from app.domain.providers.contracts import AiProviderError
from app.modules.processing_policy.claim import AI_MODEL_SLOT_PAYLOAD_KEY


@dataclass(frozen=True, slots=True)
class RrugcGeminiSlot:
    model: str
    credential_provider: str


GEMINI_MODEL_POOL_TEMPORARILY_UNAVAILABLE = (
    "gemini_model_pool_temporarily_unavailable"
)
RRUGC_GEMINI_RETRY_FALLBACK_SECONDS = 60
RRUGC_GEMINI_RETRY_MIN_SECONDS = 15


def deferred_rrugc_ai_retry(
    error: AiProviderError,
    *,
    message: str,
    now: datetime | None = None,
) -> DeferredJobOutcome | None:
    """Defer transient AI capacity failures without consuming job attempts.

    Gemini pool exhaustion is a provider-capacity condition, not a failed RRUGC
    job. Always keep the same processing job pending until capacity returns.
    Pool errors normally carry ``earliest_retry_at``; the fallback covers older
    or wrapped provider errors that only preserve the stable error code.
    """
    if not error.retryable:
        return None

    current = now or datetime.now(timezone.utc)
    retry_at = getattr(error, "earliest_retry_at", None)
    if not isinstance(retry_at, datetime):
        if error.code != GEMINI_MODEL_POOL_TEMPORARILY_UNAVAILABLE:
            return None
        retry_at = current + timedelta(seconds=RRUGC_GEMINI_RETRY_FALLBACK_SECONDS)
    elif retry_at.tzinfo is None or retry_at.utcoffset() is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    retry_floor = current + timedelta(seconds=RRUGC_GEMINI_RETRY_MIN_SECONDS)
    if retry_at < retry_floor:
        retry_at = retry_floor

    return DeferredJobOutcome(
        error.code,
        message,
        retry_at,
    )


def scheduled_rrugc_gemini_slot(job: ClaimedJob) -> RrugcGeminiSlot | None:
    """Return the claim-time Gemini reservation only when it belongs to this run."""
    marker = job.payload.get(AI_MODEL_SLOT_PAYLOAD_KEY)
    if not isinstance(marker, dict):
        return None
    model = marker.get("model")
    credential_provider = marker.get("credential_provider")
    if (
        marker.get("provider") != "gemini"
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(credential_provider, str)
        or not (
            credential_provider == "gemini"
            or credential_provider.startswith("gemini_backup_")
        )
        or marker.get("worker_id") != job.lease_owner
        or marker.get("attempt_count") != job.attempt_count
    ):
        return None
    return RrugcGeminiSlot(
        model=model.strip(),
        credential_provider=credential_provider,
    )
