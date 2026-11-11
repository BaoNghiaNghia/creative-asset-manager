"""Bounded, read-only external Image Agent capacity/evidence preview.

The authoritative queue is processing_jobs; a job-bound HMAC is NOT an idle
device heartbeat and never creates a reservable free slot. This module does
not claim, renew, reserve, retry or route any CAM job.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
from app.modules.processing.model import ProcessingJobModel

from .external_heartbeat import (
    EVIDENCE_FIELD, inspect_completed_heartbeat, observe_external_heartbeat,
)

_JOB_TYPE = "etsy_design_extract"
_DEVICE_LIMIT = 20
_OPEN_JOB_LIMIT = 160
_HISTORY_LIMIT = 32


def _utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def preview_image_agent_capacity(
    session: Session, *, tenant_id: str, heartbeat_enabled: bool,
    signing_key: bytes | None, now: datetime | None = None,
) -> dict[str, Any]:
    """Return a privacy-minimized operator view with NO dispatch authority."""
    clock = _utc(now) if now is not None else datetime.now(timezone.utc)
    if clock is None:
        raise ValueError("invalid preview clock")
    response: dict[str, Any] = {
        "tenant_id": tenant_id,
        "job_type": _JOB_TYPE,
        "observation_kind": "read_only_processing_job_snapshot",
        "heartbeat_feature_enabled": heartbeat_enabled,
        "heartbeat_proof_available": bool(
            heartbeat_enabled and signing_key is not None and len(signing_key) == 32
        ),
        "capacity_state": "unverified",
        "device_limit": _DEVICE_LIMIT,
        "open_job_limit": _OPEN_JOB_LIMIT,
        "history_scan_limit": _HISTORY_LIMIT,
        "device_count": 0,
        "device_details": [],
        "open_job_counts": {"pending": 0, "retry": 0, "processing": 0},
        "active_claim_count": 0,
        "authenticated_live_claim_count": 0,
        "historical_evidence": {
            "reviewed_completed": 0,
            "verified_signed_completed": 0,
            "recent_verified_job_ids": [],
            "sample_truncated": False,
        },
        "max_advisory_slots_per_device": 1,
        "verified_free_slots": 0,
        "reservation_created": False,
        "dispatch_allowed": False,
        "execution_enabled": False,
        "automatic_promotion_enabled": False,
    }
    agents = list(session.scalars(
        select(EtsySpyAgent).where(
            EtsySpyAgent.tenant_id == tenant_id,
            EtsySpyAgent.name.startswith(IMAGE_AGENT_NAME_PREFIX),
        ).order_by(EtsySpyAgent.id).limit(_DEVICE_LIMIT + 1)
    ))
    response["device_count"] = len(agents)
    if len(agents) > _DEVICE_LIMIT:
        response["capacity_state"] = "device_limit_exceeded"
        return response

    counts = session.execute(
        select(ProcessingJobModel.status, func.count())
        .where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == _JOB_TYPE,
            ProcessingJobModel.status.in_(("pending", "retry", "processing")),
        )
        .group_by(ProcessingJobModel.status)
    ).all()
    for status, count in counts:
        if status in response["open_job_counts"]:
            response["open_job_counts"][status] = int(count)
    if response["open_job_counts"]["processing"] > _OPEN_JOB_LIMIT:
        response["capacity_state"] = "processing_job_limit_exceeded"
        return response

    jobs = list(session.scalars(
        select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == _JOB_TYPE,
            ProcessingJobModel.status == "processing",
        ).order_by(ProcessingJobModel.created_at, ProcessingJobModel.id)
        .limit(_OPEN_JOB_LIMIT + 1)
    ))
    if len(jobs) > _OPEN_JOB_LIMIT:
        response["capacity_state"] = "processing_job_limit_exceeded"
        return response

    owner_claims = {agent.id: [] for agent in agents}
    unrecognized_owner_claims = 0
    for job in jobs:
        owner = job.claimed_by
        agent_id = owner[len("etsy-image:"):] if (
            isinstance(owner, str) and owner.startswith("etsy-image:")
        ) else None
        if agent_id in owner_claims:
            owner_claims[agent_id].append(job)
        else:
            unrecognized_owner_claims += 1

    for number, agent in enumerate(agents, start=1):
        claims = owner_claims[agent.id]
        owned_live = 0
        signed_live = 0
        stale_claims = 0
        for job in claims:
            expiry = _utc(job.lease_expires_at)
            claim_at = _utc(job.claimed_at)
            lease_live = bool(
                expiry is not None and expiry > clock and claim_at is not None
                and claim_at <= clock and not job.cancellation_requested
            )
            if not lease_live:
                stale_claims += 1
                continue
            owned_live += 1
            if heartbeat_enabled and signing_key is not None:
                verified = observe_external_heartbeat(
                    job, signing_key=signing_key,
                    agent_active=bool(agent.active), now=clock,
                )
                if verified["worker_heartbeat_authenticated"]:
                    signed_live += 1
        if not agent.active:
            state = "device_revoked"
        elif len(claims) > 1:
            state = "conflicting_processing_claims"
        elif stale_claims:
            state = "stale_or_cancelled_lease"
        elif owned_live:
            state = "verified_busy" if signed_live == 1 else "busy_unverified"
        else:
            state = "idle_job_bound_heartbeat_unavailable"
        response["device_details"].append({
            "device_number": number,
            "device_active": bool(agent.active),
            "processing_claims": len(claims),
            "live_leases": owned_live,
            "expired_or_cancelled_leases": stale_claims,
            "fresh_signed_leases": signed_live,
            "capacity_state": state,
            "verified_free_slots": 0,
            "dispatch_allowed": False,
        })
        response["active_claim_count"] += owned_live
        response["authenticated_live_claim_count"] += signed_live
    response["unrecognized_owner_claims"] = unrecognized_owner_claims

    # Historical proof remains valid after an Image Agent job completes and
    # clears its active lease. Only a bounded tail is scanned; never claim
    # that this sample counts the entire archive.
    history = list(session.scalars(
        select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == _JOB_TYPE,
            ProcessingJobModel.status == "completed",
        ).order_by(
            ProcessingJobModel.completed_at.desc(),
            ProcessingJobModel.id.desc(),
        ).limit(_HISTORY_LIMIT + 1)
    ))
    response["historical_evidence"]["sample_truncated"] = len(history) > _HISTORY_LIMIT
    history = history[:_HISTORY_LIMIT]
    response["historical_evidence"]["reviewed_completed"] = len(history)
    agent_by_id = {agent.id: agent for agent in agents}
    if heartbeat_enabled and signing_key is not None:
        for job in history:
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            signed = payload.get(EVIDENCE_FIELD)
            agent_id = signed.get("agent_id") if isinstance(signed, dict) else None
            agent = agent_by_id.get(agent_id) if isinstance(agent_id, str) else None
            valid_agent = bool(
                agent is not None and agent.tenant_id == tenant_id
                and agent.name.startswith(IMAGE_AGENT_NAME_PREFIX)
            )
            record = inspect_completed_heartbeat(
                job, signing_key=signing_key,
                agent_registered=valid_agent, now=clock,
            )
            if record["historical_heartbeat_verified"]:
                summary = response["historical_evidence"]
                summary["verified_signed_completed"] += 1
                if len(summary["recent_verified_job_ids"]) < 5:
                    summary["recent_verified_job_ids"].append(job.id)
    response["capacity_state"] = (
        "unrecognized_processing_owner"
        if unrecognized_owner_claims else
        "occupied_with_conflict"
        if any(entry["processing_claims"] > 1 for entry in response["device_details"])
        else "read_only_evidence_available"
        if response["authenticated_live_claim_count"]
        else "no_authenticated_free_capacity"
    )
    return response
