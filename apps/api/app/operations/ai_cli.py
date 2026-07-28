from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.ai_governance.model import AiBudgetReservationModel
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel

_ACTIVE_JOB_STATUSES = {"pending", "processing", "retry", "claimed", "running", "queued"}
_TERMINAL_ANALYSIS_STATUSES = {"completed", "failed", "budget_blocked"}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="AI governance maintenance operations")
    value.add_argument(
        "command", choices=["budget:repair-stale-reservations"]
    )
    value.add_argument("--tenant-id", required=True)
    value.add_argument("--older-than-minutes", type=int, default=60)
    value.add_argument("--apply", action="store_true")
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
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    args = parser().parse_args()
    result = execute(args)
    print(json.dumps(result, sort_keys=True, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
