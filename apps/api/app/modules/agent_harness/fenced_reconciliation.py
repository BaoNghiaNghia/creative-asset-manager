"""Phase 4C.3E: default-OFF, read-only fenced PNG receipt reconciliation.

This is an *inspector* of the committed PostgreSQL receipt/job/slot state,
never a writer, importer, reclaimer, file cleaner, dispatcher or approval API.
It makes recovery cases visible without promoting old epochs or stale files.
No endpoint, worker or Windows agent imports this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
from app.modules.processing.model import ProcessingJobModel

from .executor_slots import _preflight, _utc
from .fenced_output import _check_immutable, _location, _receipt_identity
from .model import AgentExecutorSlotModel, AgentImageOutputReceiptModel


@dataclass(frozen=True)
class ReceiptAudit:
    state: str
    receipt_id: str | None = None
    storage_ref: str | None = None
    manual_reconcile_required: bool = False


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _stored_blob_state(
    storage_root: Path | None, row: AgentImageOutputReceiptModel,
) -> str:
    """Validate immutable read without crossing symlinked path components.

    This is diagnostic, not an atomic open+send contract for the future viewer.
    A future viewer must open a pinned no-follow descriptor and recheck bytes.
    """
    if (
        not isinstance(storage_root, Path)
        or not storage_root.is_absolute()
        or not storage_root.is_dir()
        or storage_root.is_symlink()
    ):
        return "storage_unavailable"
    namespace = storage_root / "fenced-v1"
    shard = namespace / row.receipt_id[:2]
    if namespace.is_symlink() or shard.is_symlink():
        return "storage_path_unsafe"
    target = shard / f"{row.receipt_id}.png"
    if target.is_symlink():
        return "blob_corrupt"
    if not target.exists():
        return "blob_missing"
    if not _check_immutable(target, row.output_sha256, row.output_bytes):
        return "blob_corrupt"
    return "blob_verified"


def classify_fenced_receipt_snapshot(
    *, tenant_id: str, job: ProcessingJobModel | None,
    row: AgentImageOutputReceiptModel | None,
    slot: AgentExecutorSlotModel | None,
    agent: EtsySpyAgent | None,
    storage_root: Path | None, now: datetime | None = None,
) -> ReceiptAudit:
    """Pure classification of loaded rows, without SQL/file/database writes.

    States carrying manual_reconcile_required never permit auto-acceptance.
    """
    if job is None or job.tenant_id != tenant_id or job.job_type != "etsy_design_extract":
        return ReceiptAudit("job_not_found")
    if row is None:
        return ReceiptAudit("receipt_not_found")
    if (
        row.tenant_id != tenant_id
        or row.job_id != job.id
        or not _is_sha(row.receipt_id)
        or not _is_sha(row.output_sha256)
        or not _is_sha(row.source_sha256)
        or type(row.output_bytes) is not int
        or not 0 < row.output_bytes <= 22_000_000
        or type(row.claim_attempt_count) is not int
        or row.claim_attempt_count < 1
        or type(row.fencing_token) is not int
        or row.fencing_token < 1
        or _utc(row.claim_started_at) is None
        or row.engine not in {"chatgpt_assisted", "fast_cpu"}
        or row.receipt_id != _receipt_identity(
            tenant_id=tenant_id, agent_id=row.agent_id, job_id=row.job_id,
            claim_attempt_count=row.claim_attempt_count,
            claim_started_at=row.claim_started_at,
            fencing_token=row.fencing_token, output_sha256=row.output_sha256,
        )
    ):
        return ReceiptAudit("receipt_integrity_mismatch", manual_reconcile_required=True)

    label = dict(receipt_id=row.receipt_id, storage_ref=_location(row.receipt_id))
    if row.status not in {"prepared", "accepted"}:
        return ReceiptAudit("invalid_receipt_state", **label, manual_reconcile_required=True)
    if (row.status == "prepared" and row.accepted_at is not None) or (
        row.status == "accepted" and _utc(row.accepted_at) is None
    ):
        return ReceiptAudit("invalid_receipt_state", **label, manual_reconcile_required=True)

    # Accepted evidence survives later slot release/device revocation. It must
    # still agree with the *committed* job payload and physical bytes.
    blob = _stored_blob_state(storage_root, row)
    if row.status == "accepted":
        result = (job.payload_json or {}).get("result")
        if (
            job.status != "completed"
            or not isinstance(result, dict)
            or result.get("status") != "needs_review"
            or result.get("receipt_id") != row.receipt_id
            or result.get("fenced_storage_ref") != label["storage_ref"]
            or result.get("output_sha256") != row.output_sha256
            or result.get("source_sha256") != row.source_sha256
            or result.get("engine") != row.engine
            or result.get("print_ready") is not False
            or result.get("rights_status") != "unverified"
            or result.get("manual_qa_required") is not True
            or job.attempt_count != row.claim_attempt_count
            or _utc(job.claimed_at) != _utc(row.claim_started_at)
        ):
            return ReceiptAudit("accepted_metadata_mismatch", **label, manual_reconcile_required=True)
        if blob != "blob_verified":
            return ReceiptAudit(f"accepted_{blob}", **label, manual_reconcile_required=True)
        return ReceiptAudit("accepted_verified", **label)

    # Prepared rows never automatically promote themselves, even with a
    # physically present PNG. A previously claimed job is NOT new authority.
    clock = _utc(now) or datetime.now(timezone.utc)
    active_epoch = (
        job.status == "processing"
        and not job.cancellation_requested
        and job.claimed_by == f"etsy-image:{row.agent_id}"
        and job.attempt_count == row.claim_attempt_count
        and _utc(job.claimed_at) == _utc(row.claim_started_at)
        and _utc(job.lease_expires_at) is not None
        and _utc(job.lease_expires_at) > clock
        and agent is not None and agent.id == row.agent_id
        and agent.tenant_id == tenant_id and agent.active
        and agent.name.startswith(IMAGE_AGENT_NAME_PREFIX)
        and type(job.attempt_count) is int
        and type(job.max_attempts) is int
        and 1 <= job.attempt_count <= job.max_attempts
        and _utc(job.claimed_at) is not None
        and _utc(job.claimed_at) <= clock + timedelta(seconds=5)
        and _utc(job.lease_expires_at) > _utc(job.claimed_at)
        and slot is not None and slot.tenant_id == tenant_id
        and slot.agent_id == row.agent_id and slot.job_id == job.id
        and slot.claim_attempt_count == row.claim_attempt_count
        and _utc(slot.claim_started_at) == _utc(row.claim_started_at)
        and slot.fencing_token == row.fencing_token
        and _utc(slot.lease_expires_at) is not None
        and _utc(slot.lease_expires_at) > clock
        and _utc(slot.lease_expires_at) <= _utc(job.lease_expires_at)
    )
    if not active_epoch:
        return ReceiptAudit("prepared_stale_epoch", **label, manual_reconcile_required=True)
    if blob == "blob_verified":
        return ReceiptAudit("prepared_blob_recoverable", **label)
    if blob == "blob_missing":
        return ReceiptAudit("prepared_awaiting_blob", **label)
    return ReceiptAudit(f"prepared_{blob}", **label, manual_reconcile_required=True)


def inspect_fenced_receipt(
    session: Session, *, tenant_id: str, job_id: str,
    storage_root: Path | None = None, enabled: bool = False,
    emergency_stop: bool = True, now: datetime | None = None,
) -> ReceiptAudit:
    """Read-only tenant-scoped snapshot; default OFF and PostgreSQL only.

    No locks are held beyond the normal Session read transaction; concurrent
    changes can make the *diagnostic* stale. A mutating future recovery action
    must reacquire and validate the authoritative job/slot/receipt locks.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return ReceiptAudit(blocked)
    if emergency_stop is not False:
        return ReceiptAudit("emergency_stop")
    job = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
        ProcessingJobModel.tenant_id == tenant_id,
    ))
    if job is None or job.job_type != "etsy_design_extract":
        return ReceiptAudit("job_not_found")
    row = session.get(AgentImageOutputReceiptModel, (tenant_id, job_id))
    if row is None:
        return ReceiptAudit("receipt_not_found")
    slot = session.get(AgentExecutorSlotModel, (tenant_id, row.agent_id))
    agent = session.scalar(select(EtsySpyAgent).where(
        EtsySpyAgent.id == row.agent_id, EtsySpyAgent.tenant_id == tenant_id,
    ))
    return classify_fenced_receipt_snapshot(
        tenant_id=tenant_id, job=job, row=row, slot=slot, agent=agent,
        storage_root=storage_root, now=now,
    )
