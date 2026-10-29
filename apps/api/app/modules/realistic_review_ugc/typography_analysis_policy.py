"""G3 bounded analysis policy, pure and OFF by default.

No network calls, workers, migrations or automatic enqueues in this module.
An orchestrator must enforce tenant/rights/feature gates separately.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal


Status = Literal["pending", "running", "ready", "failed", "needs_review"]
MAX_CONCURRENCY = 2
DEFAULT_CONCURRENCY = 1
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 600)


@dataclass(frozen=True)
class AnalysisDecision:
    action: Literal["cache_hit", "dispatch", "backoff", "manual_review", "skip", "exhausted"]
    reason: str
    next_attempt_at: datetime | None = None


def analysis_decision(
    *,
    enabled: bool,
    tenant_allowed: bool,
    rights: str,
    editorial_status: str,
    status: Status,
    attempts: int,
    active_jobs: int,
    concurrency: int = DEFAULT_CONCURRENCY,
    cache_hit: bool = False,
    last_failed_at: datetime | None = None,
    now: datetime | None = None,
) -> AnalysisDecision:
    """Choose eligibility without mutating state or calling an AI provider."""
    if not enabled or not tenant_allowed:
        return AnalysisDecision("skip", "feature_disabled")
    if rights not in {"owned", "licensed", "permitted_reference"}:
        return AnalysisDecision("manual_review", "rights_unverified")
    if editorial_status in {"rejected", "archived"}:
        return AnalysisDecision("skip", "reference_inactive")
    if cache_hit:
        return AnalysisDecision("cache_hit", "versioned_source_hit")
    if status == "ready":
        return AnalysisDecision("skip", "already_ready_for_current_version")
    if status == "needs_review":
        return AnalysisDecision("manual_review", "needs_human_review")
    if status == "running":
        return AnalysisDecision("skip", "already_running")
    if not (1 <= concurrency <= MAX_CONCURRENCY) or active_jobs < 0:
        raise ValueError("Invalid worker capacity")
    if attempts < 0:
        raise ValueError("Invalid attempt count")
    if attempts >= MAX_ATTEMPTS:
        return AnalysisDecision("exhausted", "retry_budget_exhausted")
    if active_jobs >= concurrency:
        return AnalysisDecision("backoff", "capacity_full")
    if attempts and last_failed_at is not None:
        if last_failed_at.tzinfo is None:
            raise ValueError("last_failed_at must be timezone-aware")
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        next_attempt = last_failed_at + timedelta(seconds=BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)])
        if clock < next_attempt:
            return AnalysisDecision("backoff", "retry_cooldown", next_attempt)
    return AnalysisDecision("dispatch", "eligible")
