from __future__ import annotations

import hmac
import logging
import random
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcScoutAgentModel,
    RrugcScoutRunModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import CandidateSubmission
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    token_digest,
)
from app.providers.decision.jev import JevClient


_LOGGER = logging.getLogger("cam.rrugc.scout")
_JEV_SHADOW_KEY = "_jev_shadow"
_JEV_SHADOW_POLICY_VERSION = "rrugc-scout-query-shadow-v1"
_SCOUT_RUN_META_KEY = "_scout_runtime"

SCOUT_AGENT_VERSION = "rrugc-scout-v15"
SCOUT_LEASE_SECONDS = 15 * 60
SCOUT_OFFLINE_SECONDS = 45
KEYWORD_HISTORY_RUNS = 100
KEYWORD_OUTCOME_HISTORY = 2000
KEYWORD_EXPLORATION_RATE = 0.20
KEYWORD_FAILURE_PENALTY_MAX = 0.25
KEYWORD_FAILURE_CONFIDENCE_RUNS = 3
KEYWORD_LEGACY_FAILURE_WEIGHT = 0.25
KEYWORD_FULL_FAILURE_VERSION = 36
KEYWORD_SUPPRESSION_MIN_EVALUATED = 6
KEYWORD_SUPPRESSION_MIN_REFERENCE_REVIEWS = 3
KEYWORD_SUPPRESSION_MIN_RUNS = 4
KEYWORD_SUPPRESSION_MIN_SUBMITTED = 12
SCOUT_BACKOFF_JITTER = 0.10
SCOUT_EMPTY_BACKOFF_MAX_MULTIPLIER = 3.0
SCOUT_LOGIN_BACKOFF_CAP_SECONDS = 30 * 60
SCOUT_FAILURE_BACKOFF_CAP_SECONDS = 60 * 60
SCOUT_MAX_SCROLL_BATCHES = 50
# Keep autonomous Pinterest discovery from outrunning the shared Gemini
# analysis lane when provider quota is exhausted. This is a tenant-wide
# circuit breaker, NOT a change to Gemini quotas or to existing queued jobs.
SCOUT_ANALYSIS_BACKLOG_LIMIT = 200
SCOUT_ANALYSIS_BACKLOG_MIN_AGE_SECONDS = 15 * 60
# Gradually throttle new Review discovery before the hard backlog circuit breaker.
SCOUT_ANALYSIS_SOFT_LIMIT = 125
SCOUT_ANALYSIS_SOFT_MIN_AGE_SECONDS = 10 * 60
SCOUT_ANALYSIS_SOFT_CLAIM_GAP_SECONDS = 5 * 60
# Protect Review's shared Gemini capacity without starving Keyword Scout.
# The tenant-wide quote lane admits at most one start per 60 seconds under
# Review backlog; the provider's independent quota controls still apply.
KEYWORD_BACKLOG_QUOTE_MIN_INTERVAL_SECONDS = 60

def scout_analysis_backpressure(
    session: Session, tenant_id: str, *, now: datetime | None = None,
) -> dict[str, int | bool]:
    current = now or datetime.now(timezone.utc)
    backlog, oldest = session.execute(
        select(
            func.count(ProcessingJobModel.id),
            func.min(ProcessingJobModel.created_at),
        ).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "rrugc_candidate_analyze",
            ProcessingJobModel.status.in_(("pending", "retry")),
        )
    ).one()
    backlog = int(backlog or 0)
    oldest_seconds = max(
        0, int((current - _as_utc(oldest)).total_seconds())
    ) if oldest is not None else 0
    return {
        "active": (
            backlog >= SCOUT_ANALYSIS_BACKLOG_LIMIT
            and oldest_seconds >= SCOUT_ANALYSIS_BACKLOG_MIN_AGE_SECONDS
        ),
        "pending_jobs": backlog,
        "oldest_wait_seconds": oldest_seconds,
    }


def review_scout_soft_throttle(
    session: Session, tenant_id: str, *,
    pressure: dict[str, int | bool], now: datetime,
) -> bool:
    """Permit at most one new Review scan per 5 min under rising backlog.

    Existing jobs and runs are untouched. The caller holds the agent lock,
    and a tenant's last run is shared across its Scout machines.
    """
    if (int(pressure['pending_jobs']) < SCOUT_ANALYSIS_SOFT_LIMIT
            or int(pressure['oldest_wait_seconds']) < SCOUT_ANALYSIS_SOFT_MIN_AGE_SECONDS):
        return False
    recent = session.scalar(select(RrugcScoutRunModel.id).where(
        RrugcScoutRunModel.tenant_id == tenant_id,
        RrugcScoutRunModel.created_at > now - timedelta(seconds=SCOUT_ANALYSIS_SOFT_CLAIM_GAP_SECONDS),
    ).order_by(RrugcScoutRunModel.created_at.desc()).limit(1))
    return recent is not None


def keyword_quote_backlog_gate(
    session: Session,
    tenant_id: str,
    *,
    pressure: dict[str, int | bool] | None = None,
    now: datetime | None = None,
    reserve: bool = False,
) -> dict[str, int | bool | str]:
    """Permit bounded Keyword quote extraction while Review has queued jobs.

    A shared tenant-scoped database reservation enforces one quote start per
    minute whenever Review analysis is pending. The Gemini provider still
    enforces real project/model/key quota, so this is not a quota bypass.
    reserve=False is read-only; reserve=True must be committed before awaiting
    the external provider to release the shared database row lock.
    """
    current = now or datetime.now(timezone.utc)
    pressure = pressure if pressure is not None else scout_analysis_backpressure(
        session, tenant_id, now=current,
    )
    if int(pressure.get("pending_jobs") or 0) <= 0:
        return {"active": False, "retry_seconds": 0,
                "reason": "review_queue_drained"}
    limiter = AiModelRateLimitRepository(session)
    params = dict(
        tenant_id=tenant_id,
        provider="rrugc_keyword_backlog_lane",
        model="gemini",
        rpm=1,
        minimum_interval_seconds=KEYWORD_BACKLOG_QUOTE_MIN_INTERVAL_SECONDS,
        now=current,
    )
    decision = (
        limiter.reserve_start(**params) if reserve else limiter.next_start(**params)
    )
    retry_seconds = (
        max(1, int((decision.next_eligible_at - current).total_seconds()) + 1)
        if not decision.allowed else 0
    )
    return {
        "active": not decision.allowed,
        "retry_seconds": retry_seconds,
        "reason": "keyword_fair_share_wait" if not decision.allowed
                  else "keyword_fair_share_allowed",
    }


def scout_client_version_number(value: str | None) -> int | None:
    raw = str(value or "").strip().casefold()
    prefix = "rrugc-scout-v"
    if not raw.startswith(prefix):
        return None
    try:
        return int(raw[len(prefix):])
    except ValueError:
        return None


def scout_client_supports_source_plans(value: str | None) -> bool:
    version = scout_client_version_number(value)
    return version is not None and version >= 12


def _scout_run_failure_weight(run: RrugcScoutRunModel) -> float:
    """Discount legacy runtime failures while trusting versioned v36+ runs."""
    stats = run.keyword_stats_json if isinstance(run.keyword_stats_json, dict) else {}
    meta = stats.get(_SCOUT_RUN_META_KEY)
    client_version = (
        str(meta.get("client_version") or "").strip()
        if isinstance(meta, dict)
        else ""
    )
    version = scout_client_version_number(client_version)
    if version is not None and version >= KEYWORD_FULL_FAILURE_VERSION:
        return 1.0
    return KEYWORD_LEGACY_FAILURE_WEIGHT


def adaptive_scroll_batch_budget(
    configured_batches: int,
    *,
    scan_attempt_count: int,
    empty_streak: int,
) -> int:
    """Grow the search window when repeated runs revisit saturated Pin bands."""
    base = max(1, min(int(configured_batches or 1), SCOUT_MAX_SCROLL_BATCHES))
    attempts = max(0, int(scan_attempt_count or 0))
    empty = max(0, int(empty_streak or 0))
    attempt_growth = (attempts // 3) * max(1, base // 2)
    empty_growth = empty * base
    return min(
        SCOUT_MAX_SCROLL_BATCHES,
        base + max(attempt_growth, empty_growth),
    )


def _jittered_delay(seconds: float) -> int:
    factor = random.uniform(
        1.0 - SCOUT_BACKOFF_JITTER,
        1.0 + SCOUT_BACKOFF_JITTER,
    )
    return max(60, int(round(seconds * factor)))


def scout_retry_delay_seconds(
    *,
    status: str,
    error_code: str | None,
    scan_interval_seconds: int,
    empty_streak: int,
    failure_streak: int,
    created_count: int,
) -> int:
    base = max(60, int(scan_interval_seconds or 300))
    if status == "completed":
        if created_count > 0:
            return base
        multiplier = min(
            SCOUT_EMPTY_BACKOFF_MAX_MULTIPLIER,
            1.0 + 0.5 * max(1, int(empty_streak)),
        )
        return _jittered_delay(base * multiplier)

    failure_step = max(0, min(int(failure_streak) - 1, 4))
    normalized_error = (error_code or "").casefold()

    if status == "needs_login":
        raw = max(300, base * 2) * (2 ** min(failure_step, 2))
        return _jittered_delay(min(raw, SCOUT_LOGIN_BACKOFF_CAP_SECONDS))

    is_rate_limited = "429" in normalized_error or "rate" in normalized_error
    if is_rate_limited:
        raw = max(300, base * 2) * (2 ** failure_step)
        return _jittered_delay(min(raw, SCOUT_FAILURE_BACKOFF_CAP_SECONDS))

    is_transient = any(
        marker in normalized_error
        for marker in (
            "timeout",
            "network",
            "http_5",
            "scan_failed",
            "runtime_error",
        )
    )
    failure_base = max(120, base) if is_transient else max(300, base)
    raw = failure_base * (2 ** failure_step)
    cap = (
        SCOUT_FAILURE_BACKOFF_CAP_SECONDS
        if is_transient
        else SCOUT_LOGIN_BACKOFF_CAP_SECONDS
    )
    return _jittered_delay(min(raw, cap))


def keyword_lifecycle_state(
    *,
    protected: bool,
    run_count: int,
    submitted: int,
    created: int,
    approved: int,
    evaluated: int,
    ref_good: int,
    ref_bad: int,
    context_good: int = 0,
    context_wrong: int = 0,
) -> str:
    if protected:
        return "protected"

    approved_yield = approved / evaluated if evaluated else 0.0
    reference_reviews = ref_good + ref_bad
    reference_yield = (
        ref_good / reference_reviews
        if reference_reviews
        else 0.0
    )
    duplicate_rate = (
        max(0.0, (submitted - created) / submitted)
        if submitted
        else 0.0
    )

    enough_bad_reference_evidence = (
        reference_reviews >= KEYWORD_SUPPRESSION_MIN_REFERENCE_REVIEWS
        and reference_yield <= 0.25
    )
    context_reviews = context_good + context_wrong
    context_yield = (
        context_good / context_reviews
        if context_reviews
        else 0.0
    )
    enough_bad_context_evidence = (
        context_reviews >= 2
        and context_yield <= 0.25
    )
    enough_bad_approval_evidence = (
        evaluated >= KEYWORD_SUPPRESSION_MIN_EVALUATED
        and approved_yield <= 0.15
    )
    enough_duplicate_evidence = (
        run_count >= KEYWORD_SUPPRESSION_MIN_RUNS
        and submitted >= KEYWORD_SUPPRESSION_MIN_SUBMITTED
        and duplicate_rate >= 0.80
    )
    if (
        enough_bad_reference_evidence
        or enough_bad_context_evidence
        or enough_bad_approval_evidence
        or enough_duplicate_evidence
    ):
        return "suppressed"

    healthy_reference_signal = (
        reference_reviews >= KEYWORD_SUPPRESSION_MIN_REFERENCE_REVIEWS
        and reference_yield >= 0.67
    )
    healthy_context_signal = (
        context_reviews >= 2
        and context_yield >= 0.67
    )
    healthy_approval_signal = evaluated >= 6 and approved_yield >= 0.45
    if healthy_reference_signal or healthy_context_signal or healthy_approval_signal:
        return "healthy"
    return "explore"


def adaptive_search_queries(
    queries: list[str],
    runs: list[RrugcScoutRunModel],
    outcomes: list[
        tuple[str, str]
        | tuple[str, str, str | None]
        | tuple[str, str, str | None, str | None]
    ] | None = None,
    *,
    protected_queries: list[str] | None = None,
) -> list[str]:
    """Rank keywords by downstream approved and human reference yield."""
    clean = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    protected_keys = {
        query.strip().casefold()
        for query in (protected_queries or [])
        if query.strip()
    }
    if len(clean) < 2:
        return clean

    stats = {query: [0, 0, 0] for query in clean}
    failure_stats = {query: 0.0 for query in clean}
    for run in runs:
        if (
            run.status == "failed"
            and run.query in failure_stats
            and run.last_error_code == "pinterest_scan_failed"
            and int(run.created_count or 0) == 0
        ):
            failure_stats[run.query] += _scout_run_failure_weight(run)
        if run.status != "completed":
            continue
        keyword_stats = (
            run.keyword_stats_json
            if isinstance(run.keyword_stats_json, dict)
            else None
        )
        matched_query_stats = False
        if keyword_stats:
            for query, payload in keyword_stats.items():
                if query not in stats or not isinstance(payload, dict):
                    continue
                row = stats[query]
                row[0] += 1
                row[1] += int(payload.get("submitted") or 0)
                row[2] += int(payload.get("created") or 0)
                matched_query_stats = True
        if matched_query_stats:
            continue
        if run.query not in stats:
            continue
        row = stats[run.query]
        row[0] += 1
        row[1] += int(run.submitted_count or 0)
        row[2] += int(run.created_count or 0)

    outcome_stats = {query: [0, 0, 0, 0, 0, 0] for query in clean}
    useful_statuses = {"approved", "import_queued", "importing", "drive_ready"}
    for outcome in outcomes or []:
        query, status = outcome[0], outcome[1]
        reference_label = outcome[2] if len(outcome) > 2 else None
        context_label = outcome[3] if len(outcome) > 3 else None
        if query not in outcome_stats:
            continue
        outcome_stats[query][1] += 1
        if status in useful_statuses:
            outcome_stats[query][0] += 1
        if reference_label == "good":
            outcome_stats[query][2] += 1
        elif reference_label in {"bad", "ai"}:
            outcome_stats[query][3] += 1
        if context_label == "good":
            outcome_stats[query][4] += 1
        elif context_label == "wrong":
            outcome_stats[query][5] += 1

    lifecycle = {}
    for query in clean:
        run_count, submitted, created = stats[query]
        approved, evaluated, ref_good, ref_bad, context_good, context_wrong = outcome_stats[query]
        lifecycle[query] = keyword_lifecycle_state(
            protected=query.casefold() in protected_keys,
            run_count=run_count,
            submitted=submitted,
            created=created,
            approved=approved,
            evaluated=evaluated,
            ref_good=ref_good,
            ref_bad=ref_bad,
            context_good=context_good,
            context_wrong=context_wrong,
        )

    if random.random() < KEYWORD_EXPLORATION_RATE:
        exploratory = [
            query for query in clean
            if lifecycle[query] in {"explore", "suppressed"}
        ]
        steady = [query for query in clean if query not in exploratory]
        random.shuffle(exploratory)
        random.shuffle(steady)
        if exploratory:
            return [exploratory[0], *steady, *exploratory[1:]]
        return steady

    jitter = {query: random.random() * 0.05 for query in clean}

    def score(query: str) -> float:
        run_count, submitted, created = stats[query]
        discovery_yield = (created + 1.0) / (submitted + 2.0)
        approved, evaluated, ref_good, ref_bad, context_good, context_wrong = outcome_stats[query]
        approved_yield = (approved + 1.0) / (evaluated + 2.0)
        approval_confidence = min(1.0, evaluated / 8.0)
        reference_reviews = ref_good + ref_bad
        reference_yield = (ref_good + 1.0) / (reference_reviews + 2.0)
        reference_confidence = min(1.0, reference_reviews / 6.0)
        run_confidence = min(1.0, run_count / 5.0)
        novelty = 1.0 / (1.0 + run_count)
        duplicate_rate = max(0.0, (submitted - created) / submitted) if submitted else (0.5 if run_count else 0.0)
        failed_runs = failure_stats[query]
        total_execution_runs = run_count + failed_runs
        failure_rate = (
            failed_runs / total_execution_runs
            if total_execution_runs
            else 0.0
        )
        failure_confidence = min(
            1.0,
            failed_runs / float(KEYWORD_FAILURE_CONFIDENCE_RUNS),
        )
        failure_penalty = (
            KEYWORD_FAILURE_PENALTY_MAX
            * failure_rate
            * failure_confidence
        )
        quality_weight = 0.35 + 0.45 * approval_confidence
        discovery_weight = 0.40 - 0.20 * approval_confidence
        human_reference_signal = (reference_yield - 0.5) * reference_confidence
        context_reviews = context_good + context_wrong
        context_yield = (context_good + 1.0) / (context_reviews + 2.0)
        context_confidence = min(1.0, context_reviews / 4.0)
        human_context_signal = (context_yield - 0.5) * context_confidence
        lifecycle_bonus = {
            # Protected means "never suppress", not "always prioritize".
            # A broad manual anchor should not crowd out a more specific
            # generated query until human/approval evidence proves it better.
            "protected": -0.08,
            "healthy": 0.18,
            "explore": 0.00,
            "suppressed": -1.00,
        }[lifecycle[query]]
        return (
            approved_yield * quality_weight
            + discovery_yield * discovery_weight * (0.65 + 0.35 * run_confidence)
            + 0.35 * human_reference_signal
            + 0.45 * human_context_signal
            + 0.20 * novelty
            - 0.15 * duplicate_rate
            - failure_penalty
            + lifecycle_bonus
            + jitter[query]
        )

    ranked = sorted(
        clean,
        key=lambda query: (
            lifecycle[query] != "suppressed",
            score(query),
        ),
        reverse=True,
    )
    active = [
        query for query in ranked
        if lifecycle[query] != "suppressed"
    ]
    return active or ranked[:1]


def keyword_health_rows(
    queries: list[str],
    runs: list[RrugcScoutRunModel],
    outcomes: list[
        tuple[str, str]
        | tuple[str, str, str | None]
        | tuple[str, str, str | None, str | None]
    ] | None = None,
    *,
    protected_queries: list[str] | None = None,
) -> list[dict[str, int | float | str | bool]]:
    clean = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    protected_keys = {
        query.strip().casefold()
        for query in (protected_queries or [])
        if query.strip()
    }
    health: dict[str, dict[str, int | float | str]] = {
        query: {
            "query": query,
            "state": "explore",
            "protected": query.casefold() in protected_keys,
            "scans": 0,
            "found": 0,
            "new": 0,
            "duplicate": 0,
            "failed_scans": 0,
            "approved": 0,
            "ref_good": 0,
            "ref_bad": 0,
            "context_good": 0,
            "context_wrong": 0,
            "approved_yield": 0.0,
            "reference_yield": 0.0,
            "duplicate_rate": 0.0,
            "failure_rate": 0.0,
        }
        for query in clean
    }

    for run in runs:
        if (
            run.status == "failed"
            and run.query in health
            and run.last_error_code == "pinterest_scan_failed"
            and int(run.created_count or 0) == 0
        ):
            row = health[run.query]
            row["failed_scans"] = int(row["failed_scans"]) + 1
        stats = run.keyword_stats_json if isinstance(run.keyword_stats_json, dict) else None
        matched_query_stats = False
        if stats:
            for query, payload in stats.items():
                if query not in health or not isinstance(payload, dict):
                    continue
                row = health[query]
                row["scans"] = int(row["scans"]) + 1
                row["found"] = int(row["found"]) + int(payload.get("submitted") or 0)
                row["new"] = int(row["new"]) + int(payload.get("created") or 0)
                row["duplicate"] = int(row["duplicate"]) + int(payload.get("existing") or 0)
                matched_query_stats = True
        if not matched_query_stats and run.query in health:
            row = health[run.query]
            row["scans"] = int(row["scans"]) + 1
            row["found"] = int(row["found"]) + int(run.submitted_count or 0)
            row["new"] = int(row["new"]) + int(run.created_count or 0)
            row["duplicate"] = int(row["duplicate"]) + int(run.existing_count or 0)

    evaluated: dict[str, int] = {query: 0 for query in clean}
    useful_statuses = {"approved", "import_queued", "importing", "drive_ready"}
    for outcome in outcomes or []:
        query, status = outcome[0], outcome[1]
        label = outcome[2] if len(outcome) > 2 else None
        context_label = outcome[3] if len(outcome) > 3 else None
        if query not in health:
            continue
        evaluated[query] += 1
        row = health[query]
        if status in useful_statuses:
            row["approved"] = int(row["approved"]) + 1
        if label == "good":
            row["ref_good"] = int(row["ref_good"]) + 1
        elif label in {"bad", "ai"}:
            row["ref_bad"] = int(row["ref_bad"]) + 1
        if context_label == "good":
            row["context_good"] = int(row["context_good"]) + 1
        elif context_label == "wrong":
            row["context_wrong"] = int(row["context_wrong"]) + 1

    for query, row in health.items():
        evaluated_count = evaluated[query]
        approved = int(row["approved"])
        ref_good = int(row["ref_good"])
        ref_bad = int(row["ref_bad"])
        ref_total = ref_good + ref_bad
        row["approved_yield"] = round(
            approved / evaluated_count if evaluated_count else 0.0,
            4,
        )
        row["reference_yield"] = round(
            ref_good / ref_total if ref_total else 0.0,
            4,
        )
        found = int(row["found"])
        duplicate = int(row["duplicate"])
        row["duplicate_rate"] = round(
            duplicate / found if found else 0.0,
            4,
        )
        scans = int(row["scans"])
        failed_scans = int(row["failed_scans"])
        row["failure_rate"] = round(
            failed_scans / scans if scans else 0.0,
            4,
        )
        row["state"] = keyword_lifecycle_state(
            protected=bool(row["protected"]),
            run_count=int(row["scans"]),
            submitted=found,
            created=int(row["new"]),
            approved=approved,
            evaluated=evaluated_count,
            ref_good=ref_good,
            ref_bad=ref_bad,
            context_good=int(row["context_good"]),
            context_wrong=int(row["context_wrong"]),
        )

    state_order = {
        "protected": 0,
        "healthy": 1,
        "explore": 2,
        "suppressed": 3,
    }
    return sorted(
        (health[query] for query in clean),
        key=lambda row: (
            state_order.get(str(row["state"]), 9),
            -float(row["approved_yield"]),
            str(row["query"]),
        ),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def quality_pipeline_count(counts, *, auto_import: bool) -> int:
    analysis_in_flight = counts.get("analysis_queued", 0) + counts.get("analyzing", 0)
    if auto_import:
        return (
            analysis_in_flight
            + counts.get("approved", 0)
            + counts.get("import_queued", 0)
            + counts.get("importing", 0)
            + counts.get("drive_ready", 0)
        )
    return analysis_in_flight + counts.get("approved", 0)


@dataclass(frozen=True, slots=True)
class ScoutClaim:
    run: RrugcScoutRunModel
    campaign: RrugcCampaignModel
    progress: int
    pipeline_count: int
    search_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoutSubmitResult:
    created: int
    existing: int
    progress: int
    pipeline_count: int
    target_count: int
    campaign_status: str


def scout_agent_token_matches(row: RrugcScoutAgentModel, raw_token: str) -> bool:
    if not raw_token or not row.active:
        return False
    return hmac.compare_digest(row.token_hash, token_digest(raw_token))


def effective_agent_status(
    row: RrugcScoutAgentModel,
    *,
    now: datetime | None = None,
) -> str:
    if not row.active:
        return "offline"
    if row.last_seen_at is None:
        return "offline"
    current = now or datetime.now(timezone.utc)
    seen = row.last_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if current - seen > timedelta(seconds=SCOUT_OFFLINE_SECONDS):
        return "offline"
    return row.status


class RrugcAutoScoutService:
    def __init__(
        self,
        session: Session,
        *,
        jev_client: JevClient | None = None,
        jev_scout_query_enabled: bool = False,
        jev_mode: str = "shadow",
    ):
        self.session = session
        self.repository = RrugcRepository(session)
        self.jev_client = jev_client
        self.jev_scout_query_enabled = bool(jev_scout_query_enabled)
        self.jev_mode = str(jev_mode or "shadow").strip().casefold()

    def _jev_query_shadow_snapshot(
        self,
        *,
        campaign: RrugcCampaignModel,
        ordered_queries: list[str],
        runs: list[RrugcScoutRunModel],
        outcomes: list[
            tuple[str, str]
            | tuple[str, str, str | None]
            | tuple[str, str, str | None, str | None]
        ],
        protected_queries: list[str],
        progress: int,
        pipeline_count: int,
    ) -> dict | None:
        if (
            not self.jev_scout_query_enabled
            or self.jev_mode != "shadow"
            or self.jev_client is None
            or len(ordered_queries) < 2
        ):
            return None

        candidates = ordered_queries[:10]
        health_rows = keyword_health_rows(
            candidates,
            runs,
            outcomes,
            protected_queries=protected_queries,
        )
        health_by_query = {
            str(row.get("query") or ""): row
            for row in health_rows
            if str(row.get("query") or "")
        }
        choice_to_query = {
            f"q{index + 1}": query
            for index, query in enumerate(candidates)
        }
        candidate_state = []
        for choice_id, query in choice_to_query.items():
            health = health_by_query.get(query, {})
            candidate_state.append({
                "id": choice_id,
                "query": query,
                "lifecycle": str(health.get("state") or "explore"),
                "protected": bool(health.get("protected") or False),
                "scans": int(health.get("scans") or 0),
                "found": int(health.get("found") or 0),
                "new": int(health.get("new") or 0),
                "duplicate": int(health.get("duplicate") or 0),
                "approved": int(health.get("approved") or 0),
                "ref_good": int(health.get("ref_good") or 0),
                "ref_bad": int(health.get("ref_bad") or 0),
                "context_good": int(health.get("context_good") or 0),
                "context_wrong": int(health.get("context_wrong") or 0),
                "approved_yield": float(health.get("approved_yield") or 0.0),
                "reference_yield": float(health.get("reference_yield") or 0.0),
                "duplicate_rate": float(health.get("duplicate_rate") or 0.0),
            })

        baseline_query = candidates[0]
        state = {
            "policy_version": _JEV_SHADOW_POLICY_VERSION,
            "campaign": {
                "target_count": int(campaign.target_count),
                "progress": int(progress),
                "pipeline_count": int(pipeline_count),
                "remaining_pipeline": max(
                    0,
                    int(campaign.target_count) - int(pipeline_count),
                ),
                "scan_attempt_count": int(campaign.scan_attempt_count or 0),
                "scan_empty_streak": int(campaign.scan_empty_streak or 0),
                "scan_failure_streak": int(campaign.scan_failure_streak or 0),
                "discovery_mode": str(campaign.discovery_mode or "keyword"),
            },
            "search": {
                "baseline_choice": "q1",
                "candidate_count": len(candidate_state),
            },
            "candidates": candidate_state,
        }
        questions = {
            "next_query": {
                "type": "choice",
                "instructions": (
                    "Choose the existing Scout query most likely to improve "
                    "approved realistic-reference yield without drifting away "
                    "from the product context. Do not invent a new query."
                ),
                "criteria": choice_to_query,
            }
        }

        try:
            # Shadow mode gets one bounded request only. A TypeSafe outage must
            # not turn one Scout claim into a multi-timeout blocking path.
            result = self.jev_client.evaluate(
                state=state,
                questions=questions,
                max_retries=0,
            )
        except Exception as exc:  # defensive: provider bugs must fail open
            _LOGGER.warning(
                "rrugc_scout_jev_shadow_exception",
                extra={"error_type": type(exc).__name__},
            )
            return {
                "policy_version": _JEV_SHADOW_POLICY_VERSION,
                "mode": "shadow",
                "status": "fallback",
                "baseline_query": baseline_query,
                "fallback_reason": "unexpected_error",
            }

        snapshot = {
            "policy_version": _JEV_SHADOW_POLICY_VERSION,
            "mode": "shadow",
            "status": "completed" if result.ok else "fallback",
            "baseline_query": baseline_query,
            "source": result.source,
            "model": result.model,
            "input_tokens": int(result.usage.input_tokens),
            "output_tokens": int(result.usage.output_tokens),
            "latency_ms": int(result.latency_ms),
            "estimated_cost_micros": int(result.estimated_cost_micros),
            "fallback_reason": result.fallback_reason,
        }
        if not result.ok:
            return snapshot

        answers = result.answers if isinstance(result.answers, dict) else {}
        answer = answers.get("next_query")
        if not isinstance(answer, dict):
            snapshot["status"] = "invalid_answer"
            snapshot["fallback_reason"] = "missing_next_query_answer"
            return snapshot

        choice_id = str(answer.get("choice") or "")
        recommended_query = choice_to_query.get(choice_id)
        if recommended_query is None:
            snapshot["status"] = "invalid_answer"
            snapshot["fallback_reason"] = "unknown_query_choice"
            return snapshot

        confidence = answer.get("confidence")
        try:
            normalized_confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            normalized_confidence = None

        raw_probabilities = answer.get("probabilities")
        probabilities: dict[str, float] = {}
        if isinstance(raw_probabilities, dict):
            for key, value in raw_probabilities.items():
                normalized_key = str(key)
                if normalized_key not in choice_to_query:
                    continue
                try:
                    probabilities[normalized_key] = max(
                        0.0,
                        min(1.0, float(value)),
                    )
                except (TypeError, ValueError):
                    continue

        snapshot.update({
            "recommended_choice": choice_id,
            "recommended_query": recommended_query,
            "confidence": normalized_confidence,
            "probabilities": probabilities,
            "agreed_with_baseline": recommended_query == baseline_query,
        })
        return snapshot

    def _release_agent_runtime(
        self,
        row: RrugcScoutAgentModel,
        *,
        now: datetime,
        error_code: str,
    ) -> None:
        self.session.execute(
            update(RrugcScoutRunModel)
            .where(
                RrugcScoutRunModel.tenant_id == row.tenant_id,
                RrugcScoutRunModel.agent_id == row.id,
                RrugcScoutRunModel.status.in_(("claimed", "running")),
            )
            .values(
                status="cancelled",
                last_error_code=error_code,
                completed_at=now,
                last_heartbeat_at=now,
            )
        )
        self.session.execute(
            update(RrugcCampaignModel)
            .where(
                RrugcCampaignModel.tenant_id == row.tenant_id,
                RrugcCampaignModel.scan_lease_agent_id == row.id,
            )
            .values(
                scan_lease_agent_id=None,
                scan_lease_run_id=None,
                scan_lease_expires_at=None,
                scan_next_at=now,
                scout_status="offline",
            )
        )

    def _archive_agent_row(
        self,
        row: RrugcScoutAgentModel,
        *,
        now: datetime,
        error_code: str,
    ) -> None:
        self._release_agent_runtime(row, now=now, error_code=error_code)
        row.active = False
        row.status = "offline"
        row.archived_at = now

    def create_agent(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
    ) -> tuple[RrugcScoutAgentModel, str]:
        clean_name = name.strip()
        if not clean_name:
            raise RrugcError(
                "rrugc_scout_agent_name_required",
                "Scout agent name is required.",
            )

        raw_token = secrets.token_urlsafe(32)
        row = RrugcScoutAgentModel(
            tenant_id=tenant_id,
            name=clean_name,
            token_hash=token_digest(raw_token),
            status="offline",
            active=True,
            created_by_user_id=user_id,
        )
        self.repository.add_scout_agent(row)
        self.session.commit()
        self.session.refresh(row)
        return row, raw_token

    def reset_agent_pairing(
        self,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> tuple[RrugcScoutAgentModel, str]:
        row = self.repository.get_scout_agent(tenant_id, agent_id)
        if row is None or not row.active:
            raise RrugcError(
                "rrugc_scout_agent_not_found",
                "Scout Agent not found.",
                status_code=404,
            )
        now = datetime.now(timezone.utc)
        self._release_agent_runtime(
            row,
            now=now,
            error_code="scout_pairing_reset",
        )
        raw_token = secrets.token_urlsafe(32)
        row.token_hash = token_digest(raw_token)
        row.status = "offline"
        row.last_seen_at = None
        row.client_version = None
        row.machine_label = None
        row.last_error_code = None
        self.session.commit()
        self.session.refresh(row)
        return row, raw_token

    def list_agents(
        self,
        *,
        tenant_id: str,
        include_archived: bool = False,
    ) -> list[RrugcScoutAgentModel]:
        return self.repository.list_scout_agents(
            tenant_id,
            include_archived=include_archived,
        )

    def authenticate_agent(
        self,
        *,
        agent_id: str,
        raw_token: str,
        lock: bool = False,
    ) -> RrugcScoutAgentModel:
        row = (
            self.repository.lock_scout_agent_unscoped(agent_id)
            if lock
            else self.repository.get_scout_agent_unscoped(agent_id)
        )
        if row is None or not row.active or not scout_agent_token_matches(row, raw_token):
            raise RrugcError(
                "rrugc_scout_agent_authentication_failed",
                "Invalid Scout Agent credentials.",
                status_code=401,
            )
        return row

    def archive_agent(
        self,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> RrugcScoutAgentModel:
        row = self.repository.get_scout_agent(tenant_id, agent_id)
        if row is None:
            raise RrugcError(
                "rrugc_scout_agent_not_found",
                "Scout Agent not found.",
                status_code=404,
            )
        if not row.active:
            return row
        now = datetime.now(timezone.utc)
        self._archive_agent_row(
            row,
            now=now,
            error_code="scout_agent_archived",
        )
        self.session.commit()
        self.session.refresh(row)
        return row

    def configure_campaign(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
        auto_scout: bool,
        scan_interval_seconds: int,
    ) -> RrugcCampaignModel:
        if not 60 <= int(scan_interval_seconds) <= 86400:
            raise RrugcError(
                "rrugc_scout_interval_invalid",
                "Auto Scout interval must be between 60 seconds and 24 hours.",
            )
        campaign = self.repository.lock_campaign(tenant_id, campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        campaign.auto_scout = bool(auto_scout)
        campaign.scan_interval_seconds = int(scan_interval_seconds)
        if campaign.auto_scout and campaign.status == "running":
            campaign.scan_next_at = datetime.now(timezone.utc)
        else:
            campaign.scan_next_at = None
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def heartbeat(
        self,
        *,
        agent_id: str,
        raw_token: str,
        status: str,
        client_version: str | None = None,
        machine_label: str | None = None,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> RrugcScoutAgentModel:
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
            lock=True,
        )
        now = datetime.now(timezone.utc)
        agent.status = status
        agent.last_seen_at = now
        agent.client_version = (client_version or "").strip()[:64] or agent.client_version
        agent.machine_label = (machine_label or "").strip()[:160] or agent.machine_label
        agent.last_error_code = (error_code or "").strip()[:100] or None
        if run_id:
            run = self.repository.lock_scout_run(agent.tenant_id, run_id)
            if run is None or run.agent_id != agent.id:
                raise RrugcError(
                    "rrugc_scout_run_not_found",
                    "Scout run not found.",
                    status_code=404,
                )
            if run.status in {"claimed", "running"}:
                run.status = "running"
                run.last_heartbeat_at = now
                campaign = self.repository.lock_campaign(
                    agent.tenant_id,
                    run.campaign_id,
                )
                if (
                    campaign is not None
                    and campaign.scan_lease_run_id == run.id
                    and campaign.scan_lease_agent_id == agent.id
                ):
                    campaign.scan_lease_expires_at = now + timedelta(
                        seconds=SCOUT_LEASE_SECONDS
                    )
                    campaign.scout_status = status
                    campaign.scout_last_seen_at = now
        self.session.commit()
        self.session.refresh(agent)
        return agent

    def diagnostics(
        self,
        *,
        agent_id: str,
        raw_token: str,
        client_version: str | None = None,
    ) -> dict:
        started = time.monotonic()
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
        )
        now = datetime.now(timezone.utc)
        analysis_pressure = scout_analysis_backpressure(
            self.session, agent.tenant_id, now=now,
        )
        keyword_gate = keyword_quote_backlog_gate(
            self.session, agent.tenant_id, pressure=analysis_pressure, now=now,
        )
        campaigns = self.repository.list_campaigns(agent.tenant_id, limit=50)
        campaign_ids = [campaign.id for campaign in campaigns]
        counts_by_campaign = self.repository.campaign_usable_counts_many(
            agent.tenant_id,
            campaign_ids,
        )
        source_plan_only = scout_client_supports_source_plans(client_version)
        source_plan_counts = (
            self.repository.source_plan_status_counts(
                agent.tenant_id,
                campaign_ids,
            )
            if source_plan_only
            else {}
        )
        jev_shadow_enabled = (
            self.jev_scout_query_enabled
            and self.jev_mode == "shadow"
        )
        shadow_by_campaign = (
            self.repository.recent_scout_shadow_stats(
                agent.tenant_id,
                campaign_ids,
                per_campaign_limit=50,
            )
            if jev_shadow_enabled
            else {}
        )

        rows: list[dict] = []
        for campaign in campaigns:
            counts = counts_by_campaign.get(campaign.id, {})
            progress = (
                counts.get("drive_ready", 0)
                if campaign.auto_import
                else counts.get("approved", 0)
            )
            pipeline_count = quality_pipeline_count(
                counts,
                auto_import=campaign.auto_import,
            )
            plan_counts = source_plan_counts.get(campaign.id, {})
            source_plan_ready = (
                int(plan_counts.get("ready", 0)) > 0
                if source_plan_only
                else None
            )
            if campaign.status != "running":
                reason = "campaign_not_running"
            elif not campaign.auto_scout:
                reason = "auto_scout_disabled"
            elif (
                campaign.scan_lease_expires_at is not None
                and _as_utc(campaign.scan_lease_expires_at) > now
            ):
                reason = "campaign_leased"
            elif progress >= campaign.target_count:
                reason = "target_reached"
            elif pipeline_count >= campaign.target_count:
                reason = "pipeline_full"
            elif source_plan_only and not source_plan_ready:
                reason = "source_plan_not_ready"
            elif (
                campaign.scan_next_at is not None
                and _as_utc(campaign.scan_next_at) > now
            ):
                reason = "scheduled_later"
            else:
                reason = "claimable"

            if reason == "claimable" and analysis_pressure["active"]:
                reason = "analysis_backpressure"

            jev_shadow_summary = None
            if jev_shadow_enabled:
                shadow_rows = shadow_by_campaign.get(campaign.id, [])
                shadow_completed = [
                    shadow
                    for shadow in shadow_rows
                    if shadow.get("status") == "completed"
                ]
                shadow_agreed = sum(
                    1
                    for shadow in shadow_completed
                    if shadow.get("agreed_with_baseline") is True
                )
                shadow_latency = [
                    int(shadow.get("latency_ms") or 0)
                    for shadow in shadow_completed
                ]
                jev_shadow_summary = {
                    "observations": len(shadow_rows),
                    "completed": len(shadow_completed),
                    "fallback": sum(
                        1
                        for shadow in shadow_rows
                        if shadow.get("status") == "fallback"
                    ),
                    "invalid_answer": sum(
                        1
                        for shadow in shadow_rows
                        if shadow.get("status") == "invalid_answer"
                    ),
                    "agreed": shadow_agreed,
                    "disagreed": len(shadow_completed) - shadow_agreed,
                    "agreement_rate": (
                        round(shadow_agreed / len(shadow_completed), 4)
                        if shadow_completed
                        else None
                    ),
                    "avg_latency_ms": (
                        round(sum(shadow_latency) / len(shadow_latency), 1)
                        if shadow_latency
                        else None
                    ),
                    "input_tokens": sum(
                        int(shadow.get("input_tokens") or 0)
                        for shadow in shadow_rows
                    ),
                    "estimated_cost_micros": sum(
                        int(shadow.get("estimated_cost_micros") or 0)
                        for shadow in shadow_rows
                    ),
                }
            rows.append({
                "campaign_id": campaign.id,
                "name": campaign.name,
                "status": campaign.status,
                "auto_scout": bool(campaign.auto_scout),
                "reason": reason,
                "target_count": int(campaign.target_count),
                "progress": int(progress),
                "pipeline_count": int(pipeline_count),
                "scan_next_at": campaign.scan_next_at.isoformat() if campaign.scan_next_at else None,
                "scan_lease_expires_at": (
                    campaign.scan_lease_expires_at.isoformat()
                    if campaign.scan_lease_expires_at
                    else None
                ),
                "scan_lease_agent_id": campaign.scan_lease_agent_id,
                "scan_lease_run_id": campaign.scan_lease_run_id,
                "source_plan_required": source_plan_only,
                "source_plan_ready": source_plan_ready,
                "source_plan_statuses": dict(plan_counts),
                "counts": dict(counts),
                "jev_shadow": jev_shadow_summary,
            })

        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        claimable_count = sum(
            1 for row in rows if row["reason"] == "claimable"
        )
        log_extra = {
            "agent_id": agent.id,
            "tenant_id": agent.tenant_id,
            "client_version": client_version,
            "campaign_count": len(rows),
            "claimable_count": claimable_count,
            "source_plan_only": source_plan_only,
            "jev_shadow_enabled": jev_shadow_enabled,
            "duration_ms": duration_ms,
        }
        if duration_ms >= 1000:
            _LOGGER.warning("rrugc_scout_diagnostics_slow", extra=log_extra)
        else:
            _LOGGER.debug("rrugc_scout_diagnostics_complete", extra=log_extra)
        return {
            "agent_id": agent.id,
            "campaigns": rows,
            "claimable": claimable_count,
            "analysis_backpressure": analysis_pressure,
            "keyword_quote_backpressure": keyword_gate,
            "duration_ms": duration_ms,
        }

    def _reconcile_expired_scout_leases(
        self,
        tenant_id: str,
        *,
        now: datetime,
    ) -> int:
        reconciled = 0
        campaigns = self.repository.expired_scout_lease_campaigns(
            tenant_id,
            now=now,
        )
        for campaign in campaigns:
            stale_run_id = campaign.scan_lease_run_id
            stale_run = (
                self.repository.lock_scout_run(tenant_id, stale_run_id)
                if stale_run_id
                else None
            )
            if stale_run is not None and stale_run.status in {"claimed", "running"}:
                stale_run.status = "cancelled"
                stale_run.last_error_code = "scout_lease_expired"
                stale_run.completed_at = now
                stale_run.last_heartbeat_at = now

            campaign.scan_lease_agent_id = None
            campaign.scan_lease_run_id = None
            campaign.scan_lease_expires_at = None
            campaign.scan_last_completed_at = now
            campaign.scan_last_error_code = "scout_lease_expired"
            if campaign.status == "running" and campaign.auto_scout:
                campaign.scout_status = "ready"
                if campaign.scan_next_at is None or _as_utc(campaign.scan_next_at) > now:
                    campaign.scan_next_at = now
            else:
                campaign.scan_next_at = None
            reconciled += 1

        if reconciled:
            self.session.flush()
            _LOGGER.info(
                "rrugc_scout_expired_leases_reconciled",
                extra={
                    "tenant_id": tenant_id,
                    "reconciled_count": reconciled,
                },
            )
        return reconciled

    def claim(
        self,
        *,
        agent_id: str,
        raw_token: str,
        client_version: str | None = None,
        machine_label: str | None = None,
    ) -> ScoutClaim | None:
        started = time.monotonic()
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
            lock=True,
        )
        now = datetime.now(timezone.utc)
        agent.last_seen_at = now
        agent.status = "ready"
        self._reconcile_expired_scout_leases(agent.tenant_id, now=now)
        agent.client_version = (client_version or "").strip()[:64] or agent.client_version
        agent.machine_label = (machine_label or "").strip()[:160] or agent.machine_label
        agent.last_error_code = None

        analysis_pressure = scout_analysis_backpressure(
            self.session, agent.tenant_id, now=now,
        )
        if analysis_pressure["active"]:
            # Maintain the heartbeat and do not claim more campaigns while
            # the tenant's existing candidates wait for Gemini capacity.
            self.session.commit()
            _LOGGER.info(
                "rrugc_scout_claim_backpressured",
                extra={
                    "agent_id": agent.id,
                    "pending_jobs": analysis_pressure["pending_jobs"],
                    "oldest_wait_seconds": analysis_pressure["oldest_wait_seconds"],
                },
            )
            return None

        if review_scout_soft_throttle(
            self.session, agent.tenant_id, pressure=analysis_pressure, now=now,
        ):
            self.session.commit()
            _LOGGER.info(
                "rrugc_review_soft_backpressure",
                extra={"agent_id": agent.id,
                       "pending_jobs": analysis_pressure["pending_jobs"],
                       "oldest_wait_seconds": analysis_pressure["oldest_wait_seconds"]},
            )
            return None

        source_plan_only = scout_client_supports_source_plans(client_version)
        campaigns = self.repository.claimable_campaigns(
            agent.tenant_id,
            now=now,
            limit=25,
            source_plan_only=source_plan_only,
        )
        selected: RrugcCampaignModel | None = None
        progress = 0
        pipeline_count = 0
        for campaign in campaigns:
            counts = self.repository.campaign_usable_counts(campaign.tenant_id, campaign.id)
            progress = (
                counts.get("drive_ready", 0)
                if campaign.auto_import
                else counts.get("approved", 0)
            )
            if progress >= campaign.target_count:
                RrugcService(self.session).refresh_campaign_completion(campaign)
                campaign.scan_next_at = None
                continue
            pipeline_count = quality_pipeline_count(
                counts,
                auto_import=campaign.auto_import,
            )
            if pipeline_count >= campaign.target_count:
                campaign.scan_next_at = now + timedelta(
                    seconds=max(60, min(int(campaign.scan_interval_seconds or 300), 300))
                )
                continue
            selected = campaign
            break

        if selected is None:
            self.session.commit()
            _LOGGER.debug(
                "rrugc_scout_claim_empty",
                extra={
                    "agent_id": agent.id,
                    "tenant_id": agent.tenant_id,
                    "client_version": client_version,
                    "source_plan_only": source_plan_only,
                    "eligible_campaign_rows": len(campaigns),
                    "duration_ms": max(
                        0,
                        round((time.monotonic() - started) * 1000),
                    ),
                },
            )
            return None

        if selected.scan_lease_run_id:
            stale_run = self.repository.lock_scout_run(
                agent.tenant_id,
                selected.scan_lease_run_id,
            )
            if stale_run is not None and stale_run.status in {"claimed", "running"}:
                stale_run.status = "cancelled"
                stale_run.last_error_code = "scout_lease_expired"
                stale_run.completed_at = now
                stale_run.last_heartbeat_at = now

        current_queries = list(selected.search_queries_json or [selected.query])
        anchor_queries = list(
            selected.search_query_anchors_json
            or [selected.query]
        )
        outcomes = self.repository.candidate_keyword_outcomes(
            agent.tenant_id,
            selected.id,
            limit=KEYWORD_OUTCOME_HISTORY,
        )
        RrugcService(self.session).refresh_campaign_discovery(
            selected,
            outcomes=outcomes,
            commit=False,
        )
        refreshed_queries = list(selected.search_queries_json or current_queries)
        if refreshed_queries != current_queries:
            self.session.flush()

        protected_queries = (
            anchor_queries[:2]
            if selected.discovery_mode == "product_context"
            else anchor_queries
        )
        history_runs = self.repository.list_scout_runs(
            agent.tenant_id,
            campaign_id=selected.id,
            limit=KEYWORD_HISTORY_RUNS,
        )
        ordered_queries = adaptive_search_queries(
            refreshed_queries or current_queries,
            history_runs,
            outcomes,
            protected_queries=protected_queries,
        )
        selected_query = ordered_queries[0] if ordered_queries else selected.query
        jev_shadow = self._jev_query_shadow_snapshot(
            campaign=selected,
            ordered_queries=ordered_queries,
            runs=history_runs,
            outcomes=outcomes,
            protected_queries=protected_queries,
            progress=progress,
            pipeline_count=pipeline_count,
        )
        effective_scroll_batches = adaptive_scroll_batch_budget(
            selected.max_scroll_batches,
            scan_attempt_count=int(selected.scan_attempt_count or 0),
            empty_streak=int(selected.scan_empty_streak or 0),
        )
        run = RrugcScoutRunModel(
            tenant_id=agent.tenant_id,
            campaign_id=selected.id,
            agent_id=agent.id,
            status="claimed",
            query=selected_query,
            target_count=selected.target_count,
            max_scroll_batches=effective_scroll_batches,
            auto_import=selected.auto_import,
            progress_before=progress,
            keyword_stats_json={
                _SCOUT_RUN_META_KEY: {
                    "client_version": str(client_version or "").strip()[:64] or None,
                    "machine_label": str(machine_label or "").strip()[:160] or None,
                },
                **(
                    {_JEV_SHADOW_KEY: jev_shadow}
                    if jev_shadow is not None
                    else {}
                ),
            },
            last_heartbeat_at=now,
            started_at=now,
        )
        self.session.add(run)
        self.session.flush()

        selected.scan_lease_agent_id = agent.id
        selected.scan_lease_run_id = run.id
        selected.scan_lease_expires_at = now + timedelta(seconds=SCOUT_LEASE_SECONDS)
        selected.scan_last_started_at = now
        selected.scan_attempt_count = int(selected.scan_attempt_count or 0) + 1
        selected.scan_last_error_code = None
        selected.scout_status = "busy"
        selected.scout_last_seen_at = now
        agent.status = "busy"
        self.session.commit()
        self.session.refresh(run)
        self.session.refresh(selected)
        duration_ms = max(
            0,
            round((time.monotonic() - started) * 1000),
        )
        _LOGGER.info(
            "rrugc_scout_claim_created",
            extra={
                "agent_id": agent.id,
                "tenant_id": agent.tenant_id,
                "client_version": client_version,
                "campaign_id": selected.id,
                "run_id": run.id,
                "source_plan_only": source_plan_only,
                "priority_policy": "scarcity_first_v1",
                "progress": int(progress),
                "pipeline_count": int(pipeline_count),
                "target_count": int(selected.target_count),
                "query": selected_query,
                "jev_shadow_status": (
                    jev_shadow.get("status")
                    if isinstance(jev_shadow, dict)
                    else None
                ),
                "duration_ms": duration_ms,
            },
        )
        return ScoutClaim(
            run=run,
            campaign=selected,
            progress=progress,
            pipeline_count=pipeline_count,
            search_queries=tuple(ordered_queries),
        )

    def submit_candidates(
        self,
        *,
        agent_id: str,
        raw_token: str,
        run_id: str,
        submissions: list[CandidateSubmission],
        source_query: str | None = None,
    ) -> ScoutSubmitResult:
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
            lock=True,
        )
        run = self.repository.lock_scout_run(agent.tenant_id, run_id)
        if run is None or run.agent_id != agent.id:
            raise RrugcError(
                "rrugc_scout_run_not_found",
                "Scout run not found.",
                status_code=404,
            )
        if run.status not in {"claimed", "running"}:
            raise RrugcError(
                "rrugc_scout_run_closed",
                "Scout run is already closed.",
                status_code=409,
            )
        campaign = self.repository.lock_campaign(agent.tenant_id, run.campaign_id)
        if campaign is None:
            raise RrugcError(
                "campaign_not_found",
                "Campaign not found.",
                status_code=404,
            )
        if (
            campaign.scan_lease_run_id != run.id
            or campaign.scan_lease_agent_id != agent.id
        ):
            raise RrugcError(
                "rrugc_scout_lease_lost",
                "Scout run no longer owns this campaign lease.",
                status_code=409,
            )
        now = datetime.now(timezone.utc)
        if (
            campaign.scan_lease_expires_at is not None
            and _as_utc(campaign.scan_lease_expires_at) < now
        ):
            raise RrugcError(
                "rrugc_scout_lease_expired",
                "Scout campaign lease expired.",
                status_code=409,
            )
        if campaign.status != "running":
            raise RrugcError(
                "campaign_not_running",
                "Campaign is not running.",
                status_code=409,
            )

        counts_before = self.repository.campaign_usable_counts(
            campaign.tenant_id,
            campaign.id,
        )
        pipeline_before = quality_pipeline_count(
            counts_before,
            auto_import=campaign.auto_import,
        )
        remaining_pipeline_budget = max(
            0,
            int(campaign.target_count) - pipeline_before,
        )
        accepted_submissions = submissions[:remaining_pipeline_budget]
        requested_source_query = " ".join(
            str(source_query or "").split()
        )[:500]
        effective_source_query = requested_source_query or run.query
        if accepted_submissions:
            _rows, created, existing = RrugcService(self.session).ingest_candidates(
                campaign=campaign,
                submissions=accepted_submissions,
                source_query=effective_source_query,
            )
        else:
            created = 0
            existing = 0
        run.status = "running"
        submitted_now = len(accepted_submissions)
        run.submitted_count += submitted_now
        run.created_count += created
        run.existing_count += existing
        keyword_stats = dict(run.keyword_stats_json or {})
        current_stats = dict(keyword_stats.get(effective_source_query) or {})
        current_stats["submitted"] = int(current_stats.get("submitted") or 0) + submitted_now
        current_stats["created"] = int(current_stats.get("created") or 0) + int(created)
        current_stats["existing"] = int(current_stats.get("existing") or 0) + int(existing)
        keyword_stats[effective_source_query] = current_stats
        run.keyword_stats_json = keyword_stats
        run.last_heartbeat_at = now
        campaign.scan_lease_expires_at = now + timedelta(seconds=SCOUT_LEASE_SECONDS)
        campaign.scout_status = "busy"
        campaign.scout_last_seen_at = now
        agent.status = "busy"
        agent.last_seen_at = now
        counts = self.repository.campaign_usable_counts(campaign.tenant_id, campaign.id)
        progress = (
            counts.get("drive_ready", 0)
            if campaign.auto_import
            else counts.get("approved", 0)
        )
        pipeline_count = quality_pipeline_count(
            counts,
            auto_import=campaign.auto_import,
        )
        self.session.commit()
        return ScoutSubmitResult(
            created=created,
            existing=existing,
            progress=progress,
            pipeline_count=pipeline_count,
            target_count=campaign.target_count,
            campaign_status=campaign.status,
        )

    def complete(
        self,
        *,
        agent_id: str,
        raw_token: str,
        run_id: str,
        status: str,
        error_code: str | None = None,
    ) -> RrugcScoutRunModel:
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
            lock=True,
        )
        run = self.repository.lock_scout_run(agent.tenant_id, run_id)
        if run is None or run.agent_id != agent.id:
            raise RrugcError(
                "rrugc_scout_run_not_found",
                "Scout run not found.",
                status_code=404,
            )
        if run.status in {"completed", "needs_login", "failed", "cancelled"}:
            return run

        campaign = self.repository.lock_campaign(agent.tenant_id, run.campaign_id)
        now = datetime.now(timezone.utc)
        run.status = status
        run.last_error_code = (error_code or "").strip()[:100] or None
        run.completed_at = now
        run.last_heartbeat_at = now

        scheduled_delay_seconds: int | None = None
        if campaign is not None:
            counts = self.repository.campaign_usable_counts(campaign.tenant_id, campaign.id)
            progress = (
                counts.get("drive_ready", 0)
                if campaign.auto_import
                else counts.get("approved", 0)
            )
            if progress >= campaign.target_count:
                RrugcService(self.session).refresh_campaign_completion(campaign)

            if campaign.scan_lease_run_id == run.id:
                campaign.scan_lease_agent_id = None
                campaign.scan_lease_run_id = None
                campaign.scan_lease_expires_at = None

            campaign.scan_last_completed_at = now
            campaign.scan_last_error_code = run.last_error_code
            if status == "completed":
                campaign.scan_failure_streak = 0
                campaign.scan_empty_streak = (
                    int(campaign.scan_empty_streak or 0) + 1
                    if int(run.created_count or 0) == 0
                    else 0
                )
                campaign.scout_status = "ready"
            else:
                campaign.scan_failure_streak = int(
                    campaign.scan_failure_streak or 0
                ) + 1
                if status == "needs_login":
                    campaign.scout_status = "needs_login"
                else:
                    campaign.scout_status = "error"

            if campaign.status == "running" and campaign.auto_scout:
                delay = scout_retry_delay_seconds(
                    status=status,
                    error_code=run.last_error_code,
                    scan_interval_seconds=int(
                        campaign.scan_interval_seconds or 300
                    ),
                    empty_streak=int(campaign.scan_empty_streak or 0),
                    failure_streak=int(campaign.scan_failure_streak or 0),
                    created_count=int(run.created_count or 0),
                )
                scheduled_delay_seconds = delay
                campaign.scan_next_at = now + timedelta(seconds=delay)
            else:
                campaign.scan_next_at = None

        _LOGGER.info(
            "rrugc_scout_run_complete",
            extra={
                "agent_id": agent.id,
                "tenant_id": agent.tenant_id,
                "run_id": run.id,
                "campaign_id": run.campaign_id,
                "status": status,
                "error_code": run.last_error_code,
                "created_count": int(run.created_count or 0),
                "existing_count": int(run.existing_count or 0),
                "empty_streak": (
                    int(campaign.scan_empty_streak or 0)
                    if campaign is not None
                    else None
                ),
                "failure_streak": (
                    int(campaign.scan_failure_streak or 0)
                    if campaign is not None
                    else None
                ),
                "next_scan_delay_seconds": scheduled_delay_seconds,
                "next_scan_at": (
                    campaign.scan_next_at.isoformat()
                    if campaign is not None and campaign.scan_next_at
                    else None
                ),
            },
        )

        agent.last_seen_at = now
        agent.last_error_code = run.last_error_code
        agent.status = (
            "ready"
            if status == "completed"
            else "needs_login"
            if status == "needs_login"
            else "error"
        )
        self.session.commit()
        self.session.refresh(run)
        return run
