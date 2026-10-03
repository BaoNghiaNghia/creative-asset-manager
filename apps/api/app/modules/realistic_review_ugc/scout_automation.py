from __future__ import annotations

import hmac
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

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


SCOUT_AGENT_VERSION = "rrugc-scout-v14"
SCOUT_LEASE_SECONDS = 15 * 60
SCOUT_OFFLINE_SECONDS = 45
KEYWORD_HISTORY_RUNS = 100
KEYWORD_OUTCOME_HISTORY = 2000
KEYWORD_EXPLORATION_RATE = 0.20
KEYWORD_SUPPRESSION_MIN_EVALUATED = 6
KEYWORD_SUPPRESSION_MIN_REFERENCE_REVIEWS = 3
KEYWORD_SUPPRESSION_MIN_RUNS = 4
KEYWORD_SUPPRESSION_MIN_SUBMITTED = 12
SCOUT_BACKOFF_JITTER = 0.10
SCOUT_EMPTY_BACKOFF_MAX_MULTIPLIER = 3.0
SCOUT_LOGIN_BACKOFF_CAP_SECONDS = 30 * 60
SCOUT_FAILURE_BACKOFF_CAP_SECONDS = 60 * 60
SCOUT_MAX_SCROLL_BATCHES = 50


def scout_client_supports_source_plans(value: str | None) -> bool:
    raw = str(value or "").strip().casefold()
    prefix = "rrugc-scout-v"
    if not raw.startswith(prefix):
        return False
    try:
        return int(raw[len(prefix):]) >= 12
    except ValueError:
        return False


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
    for run in runs:
        if run.status != "completed":
            continue
        keyword_stats = (
            run.keyword_stats_json
            if isinstance(run.keyword_stats_json, dict)
            else None
        )
        if keyword_stats:
            for query, payload in keyword_stats.items():
                if query not in stats or not isinstance(payload, dict):
                    continue
                row = stats[query]
                row[0] += 1
                row[1] += int(payload.get("submitted") or 0)
                row[2] += int(payload.get("created") or 0)
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
        elif reference_label == "bad":
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
            "approved": 0,
            "ref_good": 0,
            "ref_bad": 0,
            "context_good": 0,
            "context_wrong": 0,
            "approved_yield": 0.0,
            "reference_yield": 0.0,
            "duplicate_rate": 0.0,
        }
        for query in clean
    }

    for run in runs:
        stats = run.keyword_stats_json if isinstance(run.keyword_stats_json, dict) else None
        if stats:
            for query, payload in stats.items():
                if query not in health or not isinstance(payload, dict):
                    continue
                row = health[query]
                row["scans"] = int(row["scans"]) + 1
                row["found"] = int(row["found"]) + int(payload.get("submitted") or 0)
                row["new"] = int(row["new"]) + int(payload.get("created") or 0)
                row["duplicate"] = int(row["duplicate"]) + int(payload.get("existing") or 0)
        elif run.query in health:
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
        elif label == "bad":
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
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

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

        now = datetime.now(timezone.utc)
        active_rows = self.repository.lock_active_scout_agents(tenant_id)
        row = active_rows[0] if active_rows else None
        for duplicate in active_rows[1:]:
            self._archive_agent_row(
                duplicate,
                now=now,
                error_code="scout_agent_superseded",
            )

        raw_token = secrets.token_urlsafe(32)
        if row is None:
            row = RrugcScoutAgentModel(
                tenant_id=tenant_id,
                name=clean_name,
                token_hash=token_digest(raw_token),
                status="offline",
                active=True,
                created_by_user_id=user_id,
            )
            self.repository.add_scout_agent(row)
        else:
            self._release_agent_runtime(
                row,
                now=now,
                error_code="scout_pairing_reset",
            )
            row.name = clean_name
            row.token_hash = token_digest(raw_token)
            row.status = "offline"
            row.last_seen_at = None
            row.client_version = None
            row.machine_label = None
            row.last_error_code = None
            row.active = True
            row.archived_at = None

        self.session.commit()
        self.session.refresh(row)
        return row, raw_token

    def list_agents(
        self,
        *,
        tenant_id: str,
        include_archived: bool = False,
    ) -> list[RrugcScoutAgentModel]:
        rows = self.repository.list_scout_agents(
            tenant_id,
            include_archived=include_archived,
        )
        if include_archived:
            return rows
        return rows[:1]

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
        if row is None or not scout_agent_token_matches(row, raw_token):
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
    ) -> dict:
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
        )
        now = datetime.now(timezone.utc)
        campaigns = self.repository.list_campaigns(agent.tenant_id, limit=50)
        rows: list[dict] = []
        for campaign in campaigns:
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
            elif (
                campaign.scan_next_at is not None
                and _as_utc(campaign.scan_next_at) > now
            ):
                reason = "scheduled_later"
            else:
                reason = "claimable"
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
                "counts": counts,
            })
        return {
            "agent_id": agent.id,
            "campaigns": rows,
            "claimable": sum(1 for row in rows if row["reason"] == "claimable"),
        }

    def claim(
        self,
        *,
        agent_id: str,
        raw_token: str,
        client_version: str | None = None,
        machine_label: str | None = None,
    ) -> ScoutClaim | None:
        agent = self.authenticate_agent(
            agent_id=agent_id,
            raw_token=raw_token,
            lock=True,
        )
        now = datetime.now(timezone.utc)
        agent.last_seen_at = now
        agent.status = "ready"
        agent.client_version = (client_version or "").strip()[:64] or agent.client_version
        agent.machine_label = (machine_label or "").strip()[:160] or agent.machine_label
        agent.last_error_code = None

        campaigns = self.repository.claimable_campaigns(
            agent.tenant_id,
            now=now,
            limit=25,
            source_plan_only=scout_client_supports_source_plans(client_version),
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
        ordered_queries = adaptive_search_queries(
            refreshed_queries or current_queries,
            self.repository.list_scout_runs(
                agent.tenant_id,
                campaign_id=selected.id,
                limit=KEYWORD_HISTORY_RUNS,
            ),
            outcomes,
            protected_queries=protected_queries,
        )
        selected_query = ordered_queries[0] if ordered_queries else selected.query
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
        campaign_queries = {
            str(query).strip().casefold(): str(query).strip()
            for query in (campaign.search_queries_json or [campaign.query])
            if str(query or "").strip()
        }
        requested_source_query = str(source_query or "").strip()
        effective_source_query = campaign_queries.get(
            requested_source_query.casefold(),
            run.query,
        )
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
                campaign.scan_next_at = now + timedelta(seconds=delay)
            else:
                campaign.scan_next_at = None

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
