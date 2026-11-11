"""Read-only ProcessingJob lease evidence for future multi-agent admission.

The existing processing_jobs row remains the sole lease authority. This
module never claims, renews, cancels, or releases a lease and never authorizes
dispatch. Recent row activity is NOT an authenticated worker heartbeat.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.modules.processing.model import ProcessingJobModel

# Exact, already-registered CAM handlers only. These mappings do not claim
# that every matching job invokes Codex, nor authorize any new worker.
_STAGE_JOB_TYPES = {
    "stage1": frozenset({
        "rrugc_creative_brief_generate",
        "rrugc_keyword_image_generate",
    }),
    "stage2": frozenset({"rrugc_stage2_generate"}),
    # Stage 4's worker/provider binding is not independently established.
    # Fail closed here until its actual pipeline execution path is reviewed.
}

_FUTURE_SKEW_SECONDS = 5
_MIN_ACTIVITY_WINDOW_SECONDS = 30
_MAX_ACTIVITY_WINDOW_SECONDS = 180


def _utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    # PostgreSQL timestamptz is aware; the SQLite test driver (and legacy
    # model dates) strips tzinfo. Application timestamps are UTC by contract.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def observe_processing_lease(
    job: ProcessingJobModel,
    *,
    stage: str,
    now: datetime | None = None,
    heartbeat_seconds: float = 15.0,
) -> dict[str, Any]:
    """Inspect an existing claimed job without disclosing its worker identity.

    An active observation is a momentary PostgreSQL lease read, NOT proof
    that a future executor owns capacity or that a provider delivered work.
    """
    checked_at = _utc(now) if now is not None else datetime.now(timezone.utc)
    if checked_at is None:
        raise ValueError("invalid observation time")
    activity_window = max(
        _MIN_ACTIVITY_WINDOW_SECONDS,
        min(_MAX_ACTIVITY_WINDOW_SECONDS, int(max(1.0, heartbeat_seconds) * 4)),
    )
    result: dict[str, Any] = {
        "state": "blocked",
        "active_lease_observed": False,
        "lease_seconds_remaining": 0,
        "recent_lease_row_activity": False,
        "worker_heartbeat_authenticated": False,
        "executor_slot_reserved": False,
        "job_type_verified_for_stage": False,
    }
    allowed_types = _STAGE_JOB_TYPES.get(stage)
    if not allowed_types or job.job_type not in allowed_types:
        result["state"] = "job_type_not_supported_for_stage"
        return result
    result["job_type_verified_for_stage"] = True
    if job.cancellation_requested:
        result["state"] = "cancellation_requested"
        return result
    if job.status != "processing":
        result["state"] = "job_not_processing"
        return result
    if not isinstance(job.claimed_by, str) or not job.claimed_by.strip():
        result["state"] = "lease_owner_missing"
        return result
    if (
        type(job.attempt_count) is not int
        or type(job.max_attempts) is not int
        or not 1 <= job.attempt_count <= job.max_attempts
    ):
        result["state"] = "attempt_not_valid"
        return result
    claim_at = _utc(job.claimed_at)
    renewal_at = _utc(job.updated_at)
    expires_at = _utc(job.lease_expires_at)
    if claim_at is None or renewal_at is None or expires_at is None:
        result["state"] = "lease_timestamps_missing"
        return result
    if claim_at > checked_at + timedelta(seconds=_FUTURE_SKEW_SECONDS):
        result["state"] = "claim_clock_invalid"
        return result
    if renewal_at < claim_at or renewal_at > checked_at + timedelta(seconds=_FUTURE_SKEW_SECONDS):
        result["state"] = "lease_activity_clock_invalid"
        return result
    if expires_at <= checked_at or expires_at <= claim_at:
        result["state"] = "lease_expired"
        return result
    if checked_at - renewal_at > timedelta(seconds=activity_window):
        result["state"] = "lease_row_activity_stale"
        return result
    result["active_lease_observed"] = True
    result["recent_lease_row_activity"] = True
    result["lease_seconds_remaining"] = max(
        1, int((expires_at - checked_at).total_seconds()),
    )
    result["state"] = "active_lease_observed"
    return result
