"""Read-only production readiness summary for CAM Agent Harness V2.

No new tables, scheduling, state writes, provider calls, model usage or
database copies. A diagnostic must not be mistaken for dispatch admission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel

# Conservative advisory: CAM backend deploy must keep space for a staged
# immutable release and must preserve the previous rollback. No auto-delete.
_MIN_RECOMMENDED_AVAILABLE_BYTES = 4 * 1024**3
_MAX_RECOMMENDED_USED_PERCENT = 90.0
_OPEN_STATUSES = ("pending", "retry", "processing")


def disk_headroom(mount: str = "/") -> dict[str, object]:
    """Read statvfs only; never traverse, cache or delete user data."""
    try:
        stat = os.statvfs(mount)
        unit = stat.f_frsize
        available = stat.f_bavail * unit
        used = (stat.f_blocks - stat.f_bfree) * unit
        if unit <= 0 or available < 0 or used < 0 or used + available <= 0:
            raise ValueError("invalid_statvfs")
        usage = round(100.0 * used / (used + available), 2)
    except (OSError, ValueError, OverflowError):
        return {"state": "unverified", "available_mib": None, "used_percent": None,
                "sufficient_for_canary": False}
    return {
        "state": "advisory_headroom" if (
            available >= _MIN_RECOMMENDED_AVAILABLE_BYTES
            and usage < _MAX_RECOMMENDED_USED_PERCENT
        ) else "disk_pressure",
        "available_mib": available // (1024 * 1024),
        "used_percent": usage,
        "sufficient_for_canary": bool(
            available >= _MIN_RECOMMENDED_AVAILABLE_BYTES
            and usage < _MAX_RECOMMENDED_USED_PERCENT
        ),
    }


def collect_processing_queue_counts(session: Session, *, tenant_id: str) -> dict[str, int]:
    """Only bounded status aggregates from authoritative processing_jobs."""
    counts = {status: 0 for status in _OPEN_STATUSES}
    rows = session.execute(
        select(ProcessingJobModel.status, func.count())
        .where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.status.in_(_OPEN_STATUSES),
        ).group_by(ProcessingJobModel.status)
    ).all()
    for state, amount in rows:
        if state in counts:
            counts[state] = max(0, int(amount))
    return counts


def collect_processing_lease_health(
    session: Session, *, tenant_id: str, now: datetime | None = None,
) -> dict[str, int | str]:
    """Aggregate active, expired and invalid claims from existing processing_jobs.

    One tenant-bound SELECT; no job bodies, IDs, writes, recovery action, new
    storage or assumed executor capacity. A snapshot cannot reserve a slot.
    """
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    else:
        clock = clock.astimezone(timezone.utc)
    invalid_claim = or_(
        ProcessingJobModel.claimed_by.is_(None),
        ProcessingJobModel.claimed_at.is_(None),
        ProcessingJobModel.lease_expires_at.is_(None),
        ProcessingJobModel.claimed_at > clock,
        ProcessingJobModel.lease_expires_at <= ProcessingJobModel.claimed_at,
    )
    expired_claim = and_(
        not_(invalid_claim), ProcessingJobModel.lease_expires_at <= clock,
    )
    statement = select(
        func.count(ProcessingJobModel.id),
        func.count(ProcessingJobModel.id).filter(invalid_claim),
        func.count(ProcessingJobModel.id).filter(expired_claim),
    ).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.status == "processing",
    )
    observed, invalid, expired = session.execute(statement).one()
    total = max(0, int(observed or 0))
    invalid_count = max(0, int(invalid or 0))
    expired_count = max(0, int(expired or 0))
    # Never manufacture positive "free" capacity from this observation.
    consistent = invalid_count + expired_count <= total
    return {
        "state": "observed" if consistent else "inconsistent",
        "observed_processing": total,
        "invalid_claims": invalid_count,
        "expired_leases": expired_count,
        "valid_live_leases": (
            total - invalid_count - expired_count if consistent else 0
        ),
    }


def summarize_readiness(*, tenant_id: str, settings: object,
                        open_jobs: dict[str, int],
                        disk: dict[str, object],
                        lease_health: dict[str, int | str] | None = None) -> dict[str, Any]:
    """Explain missing execution gates, without granting any permissions."""
    emergency = bool(getattr(settings, "AI_EMERGENCY_STOP_ENABLED", False))
    execution_configured = bool(getattr(settings, "AGENT_HARNESS_EXECUTION_ENABLED", False))
    feedback_capture = bool(getattr(settings, "AGENT_HARNESS_FEEDBACK_SYNC_ENABLED", False)
                            and getattr(settings, "AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED", False))
    feedback_processing = bool(feedback_capture and getattr(
        settings, "AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED", False))
    blockers = ["external_executor_slot_not_reserved",
                "independent_artwork_qa_not_verified"]
    if not execution_configured:
        blockers.insert(0, "harness_execution_disabled")
    if emergency:
        blockers.insert(0, "global_emergency_stop")
    if not bool(disk.get("sufficient_for_canary")):
        blockers.insert(0, "disk_headroom_unverified_or_low")
    lease = lease_health or {}
    lease_state = lease.get("state", "unverified")
    observed_leases = lease_state == "observed"
    invalid_count = int(lease.get("invalid_claims", 0)) if observed_leases else 0
    expired_count = int(lease.get("expired_leases", 0)) if observed_leases else 0
    if not observed_leases:
        blockers.insert(0, "processing_lease_health_unverified")
    elif invalid_count or expired_count:
        blockers.insert(0, "processing_claims_require_recovery")
    return {
        "tenant_id": tenant_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "evidence_kind": "read_only_operational_diagnostic",
        "disk": {
            "state": disk.get("state", "unverified"),
            "available_mib": disk.get("available_mib"),
            "used_percent": disk.get("used_percent"),
            "sufficient_for_canary": bool(disk.get("sufficient_for_canary")),
        },
        "processing_jobs": {
            name: int(open_jobs.get(name, 0)) for name in _OPEN_STATUSES
        },
        "processing_lease_health": {
            "state": lease_state if lease_state in ("observed", "inconsistent") else "unverified",
            "observed_processing": int(lease.get("observed_processing", 0)) if observed_leases else None,
            "invalid_claims": invalid_count if observed_leases else None,
            "expired_leases": expired_count if observed_leases else None,
            "valid_live_leases": int(lease.get("valid_live_leases", 0)) if observed_leases else None,
            "reservation_created": False,
            "dispatch_allowed": False,
        },
        "feedback": {
            "capture_configured": feedback_capture,
            "processing_configured": feedback_processing,
            "automatic_promotion_enabled": False,
        },
        "executor": {
            "configured_execution_flag": execution_configured,
            "verified_free_slots": 0,
            "reservation_created": False,
            "dispatch_allowed": False,
        },
        "blockers": blockers,
        "no_new_database": True,
        "no_backup_created": True,
        "automatic_cleanup_enabled": False,
    }
