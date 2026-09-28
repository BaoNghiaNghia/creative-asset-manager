from __future__ import annotations

import hmac
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_strategy import build_campaign_search_queries
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


SCOUT_AGENT_VERSION = "rrugc-scout-v3"
SCOUT_LEASE_SECONDS = 15 * 60
SCOUT_OFFLINE_SECONDS = 45
KEYWORD_HISTORY_RUNS = 100
KEYWORD_EXPLORATION_RATE = 0.20


def adaptive_search_queries(
    queries: list[str],
    runs: list[RrugcScoutRunModel],
    outcomes: list[tuple[str, str] | tuple[str, str, str | None]] | None = None,
) -> list[str]:
    """Rank keywords by downstream approved and human reference yield."""
    clean = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    if len(clean) < 2:
        return clean

    stats = {query: [0, 0, 0] for query in clean}
    for run in runs:
        if run.query not in stats or run.status != "completed":
            continue
        row = stats[run.query]
        row[0] += 1
        row[1] += int(run.submitted_count or 0)
        row[2] += int(run.created_count or 0)

    outcome_stats = {query: [0, 0, 0, 0] for query in clean}
    useful_statuses = {"approved", "import_queued", "importing", "drive_ready"}
    for outcome in outcomes or []:
        query, status = outcome[0], outcome[1]
        reference_label = outcome[2] if len(outcome) > 2 else None
        if query not in outcome_stats:
            continue
        outcome_stats[query][1] += 1
        if status in useful_statuses:
            outcome_stats[query][0] += 1
        if reference_label == "good":
            outcome_stats[query][2] += 1
        elif reference_label == "bad":
            outcome_stats[query][3] += 1

    if random.random() < KEYWORD_EXPLORATION_RATE:
        random.shuffle(clean)
        return clean

    jitter = {query: random.random() * 0.05 for query in clean}

    def score(query: str) -> float:
        run_count, submitted, created = stats[query]
        discovery_yield = (created + 1.0) / (submitted + 2.0)
        approved, evaluated, ref_good, ref_bad = outcome_stats[query]
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
        return (
            approved_yield * quality_weight
            + discovery_yield * discovery_weight * (0.65 + 0.35 * run_confidence)
            + 0.35 * human_reference_signal
            + 0.20 * novelty
            - 0.15 * duplicate_rate
            + jitter[query]
        )

    return sorted(clean, key=score, reverse=True)


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
            counts = self.repository.campaign_counts(campaign.tenant_id, campaign.id)
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

        # Older releases exponentially backed off campaigns after empty scans.
        # Collapse any legacy over-backoff immediately so an online Scout can
        # resume at the configured cadence instead of remaining idle for up to an hour.
        for campaign in self.repository.list_campaigns(agent.tenant_id, limit=200):
            if (
                campaign.status == "running"
                and campaign.auto_scout
                and int(campaign.scan_empty_streak or 0) > 0
                and campaign.scan_next_at is not None
                and campaign.scan_last_completed_at is not None
            ):
                expected_next = _as_utc(campaign.scan_last_completed_at) + timedelta(
                    seconds=max(60, int(campaign.scan_interval_seconds or 300))
                )
                if _as_utc(campaign.scan_next_at) > expected_next + timedelta(seconds=5):
                    campaign.scan_next_at = now

        campaigns = self.repository.claimable_campaigns(
            agent.tenant_id,
            now=now,
            limit=25,
        )
        selected: RrugcCampaignModel | None = None
        progress = 0
        pipeline_count = 0
        for campaign in campaigns:
            counts = self.repository.campaign_counts(campaign.tenant_id, campaign.id)
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
        outcomes = self.repository.candidate_keyword_outcomes(
            agent.tenant_id,
            selected.id,
        )
        refreshed_queries = build_campaign_search_queries(
            name=selected.name,
            queries=current_queries,
            product_snapshot=selected.product_snapshot_json,
            reject_headwear=selected.reject_headwear,
            outcomes=outcomes,
        )
        if refreshed_queries != current_queries:
            selected.query = refreshed_queries[0]
            selected.search_queries_json = refreshed_queries
            self.session.flush()

        ordered_queries = adaptive_search_queries(
            refreshed_queries or current_queries,
            self.repository.list_scout_runs(
                agent.tenant_id,
                campaign_id=selected.id,
                limit=KEYWORD_HISTORY_RUNS,
            ),
            outcomes,
        )
        selected_query = ordered_queries[0] if ordered_queries else selected.query
        run = RrugcScoutRunModel(
            tenant_id=agent.tenant_id,
            campaign_id=selected.id,
            agent_id=agent.id,
            status="claimed",
            query=selected_query,
            target_count=selected.target_count,
            max_scroll_batches=selected.max_scroll_batches,
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
        )

    def submit_candidates(
        self,
        *,
        agent_id: str,
        raw_token: str,
        run_id: str,
        submissions: list[CandidateSubmission],
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

        counts_before = self.repository.campaign_counts(
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
        if accepted_submissions:
            _rows, created, existing = RrugcService(self.session).ingest_candidates(
                campaign=campaign,
                submissions=accepted_submissions,
                source_query=run.query,
            )
        else:
            created = 0
            existing = 0
        run.status = "running"
        run.submitted_count += len(accepted_submissions)
        run.created_count += created
        run.existing_count += existing
        run.last_heartbeat_at = now
        campaign.scan_lease_expires_at = now + timedelta(seconds=SCOUT_LEASE_SECONDS)
        campaign.scout_status = "busy"
        campaign.scout_last_seen_at = now
        agent.status = "busy"
        agent.last_seen_at = now
        counts = self.repository.campaign_counts(campaign.tenant_id, campaign.id)
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
            counts = self.repository.campaign_counts(campaign.tenant_id, campaign.id)
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
                campaign.scan_empty_streak = (
                    int(campaign.scan_empty_streak or 0) + 1
                    if run.created_count == 0
                    else 0
                )
                campaign.scout_status = "ready"
            elif status == "needs_login":
                campaign.scout_status = "needs_login"
            else:
                campaign.scout_status = "error"

            if campaign.status == "running" and campaign.auto_scout:
                if status == "needs_login":
                    delay = 60
                elif status == "failed":
                    delay = max(120, campaign.scan_interval_seconds)
                else:
                    delay = max(60, int(campaign.scan_interval_seconds or 300))
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
