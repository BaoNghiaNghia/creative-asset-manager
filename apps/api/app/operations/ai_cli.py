from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select

from app.core.database import SessionLocal
from app.modules.ai_governance.model import (
    AiBudgetReservationModel,
    AiModelRateLimitStateModel,
    AiUsageRecordModel,
    GeminiProjectQuotaStateModel,
)
from app.modules.ai_governance.rate_limit import configured_model_rates
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.claim import TenantAwareJobClaimer

_ACTIVE_JOB_STATUSES = {"pending", "processing", "retry", "claimed", "running", "queued"}
_TERMINAL_ANALYSIS_STATUSES = {"completed", "failed", "budget_blocked"}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="AI governance maintenance operations")
    value.add_argument(
        "command",
        choices=[
            "budget:repair-stale-reservations",
            "rate-limit:repair",
            "rate-limit:validate",
            "model-gate:repair",
        ],
    )
    value.add_argument("--tenant-id", required=True)
    value.add_argument("--older-than-minutes", type=int, default=60)
    value.add_argument(
        "--deployed-after",
        help="ISO-8601 deployment timestamp; required by model-gate:repair",
    )
    value.add_argument("--apply", action="store_true")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--output-json", action="store_true")
    return value


def repair_stale_reservations(
    *,
    tenant_id: str,
    older_than_minutes: int = 60,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if older_than_minutes < 1:
        raise ValueError("--older-than-minutes must be positive")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=older_than_minutes)
    reasons: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    scanned = repaired = skipped = 0
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(AiBudgetReservationModel)
                .where(
                    AiBudgetReservationModel.tenant_id == tenant_id,
                    AiBudgetReservationModel.status == "reserved",
                    AiBudgetReservationModel.created_at < cutoff,
                )
                .order_by(AiBudgetReservationModel.created_at, AiBudgetReservationModel.id)
            )
        )
        for reservation in rows:
            scanned += 1
            job = (
                session.get(ProcessingJobModel, reservation.job_id)
                if reservation.job_id
                else None
            )
            analysis = (
                session.get(AssetAiAnalysisModel, reservation.analysis_id)
                if reservation.analysis_id
                else None
            )
            if job is not None and job.tenant_id != tenant_id:
                reasons["tenant_mismatch_skipped"] += 1
                skipped += 1
                continue
            if analysis is not None and analysis.tenant_id != tenant_id:
                reasons["tenant_mismatch_skipped"] += 1
                skipped += 1
                continue
            if job is not None and job.status in _ACTIVE_JOB_STATUSES:
                reasons["active_job_skipped"] += 1
                skipped += 1
                continue
            if job is not None and job.status in {"completed", "failed"}:
                reason = "terminal_job"
            elif analysis is not None and analysis.status in _TERMINAL_ANALYSIS_STATUSES:
                reason = "terminal_analysis"
            elif job is None and analysis is None:
                reason = "orphaned_references"
            else:
                reasons["not_terminal_skipped"] += 1
                skipped += 1
                continue
            reasons[reason] += 1
            if apply:
                released = AiBudgetService(
                    AiGovernanceRepository(session), _settings_for_cli()
                ).release(reservation.id, reason=f"stale_reservation:{reason}", now=now)
                statuses[released.status] += 1
                repaired += 1
            else:
                statuses[reservation.status] += 1
        if apply:
            session.commit()
        else:
            session.rollback()
    return {
        "tenant_id": tenant_id,
        "dry_run": not apply,
        "older_than_minutes": older_than_minutes,
        "scanned": scanned,
        "repaired": repaired,
        "skipped": skipped,
        "repair_reasons": dict(sorted(reasons.items())),
        "resulting_statuses": dict(sorted(statuses.items())),
    }


def repair_rate_limit_backlog(
    *,
    tenant_id: str,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Clear stale local scheduling errors without changing attempt history."""
    now = now or datetime.now(timezone.utc)
    repaired = released_accounting = skipped_active = 0
    with SessionLocal() as session:
        jobs = list(
            session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.last_error_code == "ai_model_rate_limited",
                )
                .order_by(ProcessingJobModel.created_at, ProcessingJobModel.id)
            )
        )
        candidates = [job for job in jobs if job.status in {"pending", "retry", "queued"}]
        skipped_active = sum(
            job.status in {"processing", "claimed", "running"} for job in jobs
        )
        due_jobs = sum(
            job.next_attempt_at is None or _as_utc(job.next_attempt_at) <= now
            for job in candidates
        )
        future_jobs = len(candidates) - due_jobs
        error_fields_to_clear = sum(
            bool(job.last_error_code or job.last_error_message) for job in candidates
        )
        leases_to_release = sum(
            bool(
                job.concurrency_accounted
                or job.claimed_by
                or job.claimed_at
                or job.lease_expires_at
            )
            for job in candidates
        )
        if apply:
            claimer = TenantAwareJobClaimer(session)
            for job in candidates:
                if job.concurrency_accounted:
                    claimer.release(job)
                    released_accounting += 1
                job.status = "pending"
                job.next_attempt_at = now
                job.claimed_by = None
                job.claimed_at = None
                job.lease_expires_at = None
                job.last_error_code = None
                job.last_error_message = None
                job.updated_at = now
                repaired += 1
            session.commit()
        else:
            session.rollback()
    return {
        "tenant_id": tenant_id,
        "dry_run": not apply,
        "matching_jobs": len(jobs),
        "repair_candidates": len(candidates),
        "due_jobs": due_jobs,
        "future_jobs": future_jobs,
        "processing_jobs": skipped_active,
        "repaired": repaired,
        "leases_or_accounting_to_release": leases_to_release,
        "error_fields_to_clear": error_fields_to_clear,
        "released_accounting": released_accounting,
        "active_jobs_skipped": skipped_active,
        "jobs_would_be_made_normally_claimable": len(candidates),
        "jobs_made_normally_claimable": repaired,
    }


def repair_model_gate_regression(
    *,
    tenant_id: str,
    deployed_after: datetime,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return only confirmed d2fad87 model-gate regressions to the queue."""
    deployed_after = _as_utc(deployed_after)
    now = now or datetime.now(timezone.utc)
    repaired = 0
    with SessionLocal() as session:
        jobs = list(
            session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.status == "retry",
                    ProcessingJobModel.last_error_code
                    == "gemini_model_pool_exhausted",
                    ProcessingJobModel.last_error_message
                    == "No Gemini model is currently available.",
                    or_(
                        ProcessingJobModel.created_at >= deployed_after,
                        ProcessingJobModel.updated_at >= deployed_after,
                    ),
                )
                .order_by(ProcessingJobModel.created_at, ProcessingJobModel.id)
            )
        )
        if apply:
            for job in jobs:
                job.status = "pending"
                job.next_attempt_at = now
                job.claimed_by = None
                job.claimed_at = None
                job.lease_expires_at = None
                job.last_error_code = None
                job.last_error_message = None
                job.updated_at = now
                repaired += 1
            session.commit()
        else:
            session.rollback()
    return {
        "tenant_id": tenant_id,
        "dry_run": not apply,
        "deployed_after": deployed_after,
        "matching_jobs": len(jobs),
        "repaired": repaired,
        "jobs_would_return_to_queue": len(jobs),
    }


def validate_rate_limits(
    *,
    tenant_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only diagnostic for shared AI model start scheduling state."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=5)
    settings = _settings_for_cli()
    models: list[dict[str, Any]] = []
    with SessionLocal() as session:
        states = {
            (row.provider, row.model): row
            for row in session.scalars(
                select(AiModelRateLimitStateModel).where(
                    AiModelRateLimitStateModel.tenant_id == tenant_id
                )
            )
        }
        configured = list(
            configured_model_rates(settings, "gemini", settings.GEMINI_MODEL)
        )
        configured_keys = {("gemini", model) for model, _ in configured}
        configured.extend(
            (model, rpm)
            for (provider, model), rpm in sorted(settings.ai_model_rpm_limits.items())
            if provider == "gemini" and (provider, model) not in configured_keys
        )
        model_rates = [("gemini", model, rpm) for model, rpm in configured]
        model_rates.extend(
            (provider, model, rpm)
            for (provider, model), rpm in sorted(settings.ai_model_rpm_limits.items())
            if provider != "gemini"
        )
        for provider, model, rpm in model_rates:
            state = states.get((provider, model))
            interval = max(float(settings.AI_JOB_MIN_INTERVAL_SECONDS), 60.0 / rpm)
            next_eligible = _as_utc(state.next_eligible_at) if state else now
            blocked_until = (
                _as_utc(state.blocked_until)
                if state and state.blocked_until
                else None
            )
            effective_next = max(next_eligible, blocked_until or now)
            models.append(
                {
                    "provider": provider,
                    "model": model,
                    "rpm": rpm,
                    "effective_interval_seconds": interval,
                    "last_started_at": state.last_started_at if state else None,
                    "next_eligible_at": next_eligible,
                    "blocked_until": blocked_until,
                    "effective_next_eligible_at": effective_next,
                    "available_now": effective_next <= now,
                }
            )
        local_jobs = list(
            session.scalars(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.status.in_(("pending", "retry", "queued")),
                    ProcessingJobModel.last_error_code == "ai_model_rate_limited",
                )
            )
        )
        due_local = sum(
            job.next_attempt_at is None or _as_utc(job.next_attempt_at) <= now
            for job in local_jobs
        )
        valid_future_local = len(local_jobs) - due_local
        local_defers_last_5m = int(
            session.scalar(
                select(func.count(ProcessingJobModel.id)).where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.last_error_code == "ai_model_rate_limited",
                    ProcessingJobModel.updated_at >= cutoff,
                )
            )
            or 0
        )
        provider_attempts_last_5m = int(
            session.scalar(
                select(func.count(AiUsageRecordModel.id)).where(
                    AiUsageRecordModel.tenant_id == tenant_id,
                    AiUsageRecordModel.provider == "gemini",
                    AiUsageRecordModel.occurred_at >= cutoff,
                )
            )
            or 0
        )
        completions_last_5m = int(
            session.scalar(
                select(func.count(AssetAiAnalysisModel.id)).where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.status == "completed",
                    AssetAiAnalysisModel.completed_at >= cutoff,
                )
            )
            or 0
        )
        all_models_blocked = bool(models) and not any(
            model["available_now"] for model in models if model["provider"] == "gemini"
        )
        recent_claims = int(
            session.scalar(
                select(func.count(ProcessingJobModel.id)).where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.status.in_(("processing", "claimed", "running")),
                    ProcessingJobModel.claimed_at >= cutoff,
                )
            )
            or 0
        )
        real_429_count = int(
            session.scalar(
                select(func.count(ProcessingJobModel.id)).where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.last_error_code.in_(
                        (
                            "ai_provider_rate_limited",
                            "gemini_quota_deferred",
                            "gemini_model_pool_temporarily_unavailable",
                        )
                    ),
                )
            )
            or 0
        )
        project = session.get(
            GeminiProjectQuotaStateModel,
            {
                "quota_scope": settings.GEMINI_PROJECT_QUOTA_SCOPE,
                "model": "__project_total__",
            },
        )
    eligible_models = [
        model["model"]
        for model in models
        if model["provider"] == "gemini" and model["available_now"]
    ]
    future_slots = [
        model["effective_next_eligible_at"]
        for model in models
        if model["provider"] == "gemini" and not model["available_now"]
    ]
    return {
        "tenant_id": tenant_id,
        "checked_at": now,
        "models": models,
        "queue_gate": {
            "eligible_models": eligible_models,
            "eligible_model_exists": bool(eligible_models),
            "worker_should_claim_one_analyze_job": bool(eligible_models),
            "earliest_next_slot": min(future_slots) if future_slots else None,
            "all_models_blocked": all_models_blocked,
        },
        "local_rate_deferred_jobs": len(local_jobs),
        "stale_due_local_deferred_jobs": due_local,
        "improperly_eligible_local_deferred_jobs": due_local,
        "valid_future_local_deferred_jobs": valid_future_local,
        "local_defers_last_5m": local_defers_last_5m,
        "provider_attempt_records_last_5m": provider_attempts_last_5m,
        "successful_completions_last_5m": completions_last_5m,
        "recent_analyze_claims": recent_claims,
        "real_provider_rate_or_quota_events": real_429_count,
        "project_quota": {
            "quota_scope": settings.GEMINI_PROJECT_QUOTA_SCOPE,
            "reserved_requests": project.reserved_requests if project else 0,
            "daily_limit": settings.GEMINI_PROJECT_DAILY_REQUEST_LIMIT,
            "quota_day": project.quota_day if project else None,
            "blocked_until": project.blocked_until if project else None,
        },
        "invariants": {
            "no_stale_due_local_defers": due_local == 0,
            "no_claims_while_all_models_blocked": not all_models_blocked
            or recent_claims == 0,
            "project_quota_within_limit": (
                settings.GEMINI_PROJECT_DAILY_REQUEST_LIMIT is None
                or project is None
                or project.reserved_requests
                <= settings.GEMINI_PROJECT_DAILY_REQUEST_LIMIT
            ),
        },
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--deployed-after must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--deployed-after must include a timezone")
    return parsed.astimezone(timezone.utc)


def _settings_for_cli():
    # Imported lazily so command argument parsing does not trigger app startup.
    from app.core.config import get_settings

    return get_settings()


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "budget:repair-stale-reservations":
        return repair_stale_reservations(
            tenant_id=args.tenant_id,
            older_than_minutes=args.older_than_minutes,
            apply=args.apply,
        )
    if args.command == "rate-limit:repair":
        if args.apply and args.dry_run:
            raise ValueError("--apply and --dry-run cannot be used together")
        return repair_rate_limit_backlog(
            tenant_id=args.tenant_id,
            apply=args.apply,
        )
    if args.command == "rate-limit:validate":
        return validate_rate_limits(tenant_id=args.tenant_id)
    if args.command == "model-gate:repair":
        if args.apply and args.dry_run:
            raise ValueError("--apply and --dry-run cannot be used together")
        if not args.deployed_after:
            raise ValueError("--deployed-after is required for model-gate:repair")
        return repair_model_gate_regression(
            tenant_id=args.tenant_id,
            deployed_after=_parse_datetime(args.deployed_after),
            apply=args.apply,
        )
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    args = parser().parse_args()
    result = execute(args)
    print(json.dumps(result, sort_keys=True, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
