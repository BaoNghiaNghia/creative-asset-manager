from __future__ import annotations

from dataclasses import dataclass

from app.domain.processing.handlers import ClaimedJob
from app.modules.processing_policy.claim import AI_MODEL_SLOT_PAYLOAD_KEY


@dataclass(frozen=True, slots=True)
class RrugcGeminiSlot:
    model: str
    credential_provider: str


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
