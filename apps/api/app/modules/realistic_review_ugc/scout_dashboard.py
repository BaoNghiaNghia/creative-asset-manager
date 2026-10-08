"""Read-only, tenant-isolated Scout Manager job counters.

Review Scout runs are distinct from Stage 1 AI processing jobs; Keyword
Scout works on leased Pinterest queries (not processing_jobs). All counters
are explicitly labelled by their actual source and time window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from .model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel,
    RrugcScoutMetricCycleModel, RrugcScoutQueryModel, RrugcScoutRunModel,
)
from .service import ANALYZE_JOB_TYPE, IMPORT_JOB_TYPE
from .query_intelligence import blocked_query_keywords, query_readiness


def _scalar_count(session: Session, *filters) -> int:
    return int(session.scalar(select(func.count()).select_from(filters[0]).where(*filters[1:])) or 0)


def scout_jobs_snapshot(
    session: Session, tenant_id: str, agent_id: str, *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    hour = now - timedelta(hours=1)

    # Review Pinterest scan runs are scoped to this exact paired agent.
    review = RrugcScoutRunModel
    rbase = (review.tenant_id == tenant_id, review.agent_id == agent_id)
    rcounts = dict(session.execute(select(
        review.status, func.count(review.id),
    ).where(*rbase).group_by(review.status)).all())
    active_review = sum(int(rcounts.get(status, 0)) for status in ("claimed", "running", "processing"))
    completed_review = _scalar_count(
        session, review, *rbase,
        review.status == "completed", review.updated_at >= day,
    )
    failed_review = _scalar_count(
        session, review, *rbase,
        review.status.in_(("failed", "cancelled")), review.updated_at >= day,
    )

    # Stage 1 analyze/import are durable server jobs shared by the tenant.
    job = ProcessingJobModel
    jbase = (job.tenant_id == tenant_id, job.job_type.in_((ANALYZE_JOB_TYPE, IMPORT_JOB_TYPE)))
    job_counts = dict(session.execute(select(job.status, func.count(job.id))
        .where(*jbase).group_by(job.status)).all())
    stage1_pending = int(job_counts.get("pending", 0)) + int(job_counts.get("retry", 0))
    stage1_running = int(job_counts.get("processing", 0))
    # Due timestamp is distinct from actual quota/concurrency eligibility.
    # Showing both avoids misdiagnosing a momentary Processing=0 as a stall.
    stage1_due_now = _scalar_count(
        session, job, *jbase,
        job.status.in_(("pending", "retry")),
        job.next_attempt_at <= now,
        job.cancellation_requested.is_(False),
        job.attempt_count < job.max_attempts,
    )
    stage1_retry_later = _scalar_count(
        session, job, *jbase,
        job.status.in_(("pending", "retry")),
        job.next_attempt_at > now,
    )
    stage1_completed = _scalar_count(
        session, job, *jbase,
        job.status == "completed", job.updated_at >= day,
    )
    stage1_failed = _scalar_count(
        session, job, *jbase,
        job.status == "failed", job.updated_at >= day,
    )
    stage1_completed_hour = _scalar_count(
        session, job, *jbase,
        job.status == "completed", job.updated_at >= hour,
    )
    stage1_deferred = _scalar_count(
        session, job, *jbase,
        job.status.in_(("pending", "retry")),
        job.last_error_code == "gemini_model_pool_temporarily_unavailable",
    )
    oldest_pending = session.scalar(select(func.min(job.created_at)).where(
        *jbase, job.status.in_(("pending", "retry")),
    ))
    if oldest_pending is not None:
        oldest_pending = oldest_pending.replace(tzinfo=timezone.utc) if oldest_pending.tzinfo is None else oldest_pending
    oldest_wait_minutes = max(0, int((now - oldest_pending).total_seconds() // 60)) if oldest_pending else 0

    # Keyword Scout runs leased queries; a leased query is an active search
    # job and is not the same as a queued Stage 1 Gemini job.
    query = RrugcScoutQueryModel
    qbase = (query.tenant_id == tenant_id,)
    leased = (query.lease_expires_at.is_not(None), query.lease_expires_at > now)
    # A 45-minute valid lease can outlive a browser crash. Only call a lease
    # "active" when it was claimed/renewed in the last 10 minutes. Display
    # older outstanding leases separately instead of calling them jobs.
    fresh = query.updated_at >= now - timedelta(minutes=10)
    agent_active = _scalar_count(
        session, query, *qbase, *leased, fresh,
        query.claimed_by_agent_id == agent_id,
    )
    global_active = _scalar_count(session, query, *qbase, *leased, fresh)
    older_leases = _scalar_count(
        session, query, *qbase, *leased, query.updated_at < now - timedelta(minutes=10),
    )
    # Calculate exactly the same eligibility as claim_query, including
    # exponential dry-query cooldown and blocked manual suggestions.
    # The former fixed 90-minute cutoff overstated ready-to-claim queries.
    blocked = blocked_query_keywords(session, tenant_id)
    query_rows = session.scalars(select(query).where(*qbase)).all()
    readiness_counts = {"ready": 0, "cooldown": 0, "blocked": 0, "leased": 0}
    for row in query_rows:
        readiness_counts[query_readiness(row, now, blocked)] += 1
    available = readiness_counts["ready"]
    total_queries = len(query_rows)
    # Durable counters are cumulative, not a false 24h promise.
    cycle_totals = session.execute(select(
        func.coalesce(func.sum(query.completed_cycles), 0),
        func.coalesce(func.sum(query.failed_cycles), 0),
    ).where(*qbase)).one()
    metric = RrugcScoutMetricCycleModel
    last_day_metrics = session.execute(select(
        func.coalesce(func.sum(metric.new_keywords), 0),
        func.coalesce(func.sum(metric.scanned_pins), 0),
        func.max(metric.created_at),
    ).where(
        metric.tenant_id == tenant_id, metric.agent_id == agent_id,
        metric.mode == "keyword", metric.created_at >= day,
    )).one()
    feedback = RrugcScoutFeedbackModel
    feedback_pending = _scalar_count(
        session, feedback,
        feedback.tenant_id == tenant_id,
        feedback.status == "suggested",
        feedback.processed_at.is_(None),
    )
    added_24h = _scalar_count(
        session, RrugcKeywordVolumeModel,
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.created_at >= day,
    )
    added_hour = _scalar_count(
        session, RrugcKeywordVolumeModel,
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.created_at >= hour,
    )
    return {
        "review": {
            "scout_active": active_review,
            "scout_completed_24h": completed_review,
            "scout_failed_24h": failed_review,
            "stage1_pending": stage1_pending,
            "stage1_running": stage1_running,
            "stage1_due_now": stage1_due_now,
            "stage1_retry_later": stage1_retry_later,
            "stage1_completed_24h": stage1_completed,
            "stage1_failed_24h": stage1_failed,
            "stage1_completed_1h": stage1_completed_hour,
            "stage1_deferred_gemini": stage1_deferred,
            "stage1_oldest_wait_minutes": oldest_wait_minutes,
        },
        "keyword": {
            "active_searches": agent_active,
            "tenant_active_searches": global_active,
            "older_outstanding_leases": older_leases,
            "ready_queries": available,
            "cooling_queries": readiness_counts["cooldown"],
            "blocked_queries": readiness_counts["blocked"],
            "leased_queries": readiness_counts["leased"],
            "total_queries": total_queries,
            "completed_cycles_total": int(cycle_totals[0]),
            "failed_cycles_total": int(cycle_totals[1]),
            "suggestions_pending": feedback_pending,
            "new_keywords_24h": added_24h,
            "new_keywords_1h": added_hour,
            "scanned_pins_24h_agent": int(last_day_metrics[1]),
            "saved_keywords_24h_agent": int(last_day_metrics[0]),
            "last_cycle_at": (
                last_day_metrics[2].isoformat()
                if last_day_metrics[2] is not None else None
            ),
        },
        "fetched_at": now.isoformat(),
    }
