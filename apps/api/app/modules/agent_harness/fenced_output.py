"""Inert Phase 4C.3D: crash-recoverable, fenced PNG output receipt protocol.

No HTTP route, worker or Windows relay imports this module. No process
invokes it with enabled=True. A future caller MUST validate review/source
provenance before enabling acceptance; these helpers never attest a browser,
model provider, trademark, print readiness or image ownership.

Source lock -> active paired device -> slot lock -> receipt row. Callers own
the SQLAlchemy Session transaction. A prepared receipt is durable only after
the caller commits. A received PNG is content-addressed and fsync'd before
the receipt and ProcessingJob become accepted in ONE database commit. If that
commit aborts the already-fsynced blob is an orphan, never a published output;
an exact same-claim retry may validate and reuse it. Old epochs cannot
promote or silently replace a prepared result. Never auto-delete old receipts.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel

from .executor_slots import (
    _active_image_agent, _preflight, _utc, verify_image_output_fence,
)
from .model import AgentExecutorSlotModel, AgentImageOutputReceiptModel

MAX_FENCED_PNG_BYTES = 22_000_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class FencedReceiptDecision:
    state: str
    receipt_id: str | None = None
    output_sha256: str | None = None
    storage_relative_path: str | None = None


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _valid_size(value: object) -> bool:
    return type(value) is int and 0 < value <= MAX_FENCED_PNG_BYTES


def _receipt_identity(*, tenant_id: str, agent_id: str, job_id: str,
                      claim_attempt_count: int, claim_started_at: datetime,
                      fencing_token: int, output_sha256: str) -> str:
    # Explicit, canonical framing avoids accidental string concatenation
    # collisions when tenant identifiers include ':' or other delimiters.
    encoded = json.dumps(
        ["cam-fenced-output-v1", tenant_id, agent_id, job_id,
         claim_attempt_count, _utc(claim_started_at).isoformat(),
         fencing_token, output_sha256],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _location(receipt_id: str) -> str:
    return f"fenced-v1/{receipt_id[:2]}/{receipt_id}.png"


def _matches(row: AgentImageOutputReceiptModel, job: ProcessingJobModel, *,
             agent_id: str, fencing_token: int, output_sha256: str,
             source_sha256: str, engine: str, output_bytes: int) -> bool:
    return (
        row.agent_id == agent_id
        and row.job_id == job.id
        and row.fencing_token == fencing_token
        and row.claim_attempt_count == job.attempt_count
        and _utc(row.claim_started_at) == _utc(job.claimed_at)
        and row.output_sha256 == output_sha256
        and row.source_sha256 == source_sha256
        and row.engine == engine
        and row.output_bytes == output_bytes
        and row.receipt_id == _receipt_identity(
            tenant_id=job.tenant_id, agent_id=agent_id, job_id=job.id,
            claim_attempt_count=job.attempt_count,
            claim_started_at=job.claimed_at,
            fencing_token=fencing_token, output_sha256=output_sha256,
        )
    )


def prepare_fenced_image_receipt(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, output_sha256: str, output_bytes: int,
    source_sha256: str, engine: str,
    enabled: bool = False, emergency_stop: bool = True,
    now: datetime | None = None,
) -> FencedReceiptDecision:
    """Reserve a single expected output for the current claim, DB-only.

    Must commit this prepared row before the independent blob staging phase;
    never reinterpret a prepared older attempt as the new attempt's receipt.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return FencedReceiptDecision(blocked)
    if emergency_stop is not False:
        return FencedReceiptDecision("emergency_stop")
    if (
        not _valid_sha(output_sha256) or not _valid_sha(source_sha256)
        or not _valid_size(output_bytes)
        or not isinstance(engine, str)
        or engine not in {"chatgpt_assisted", "fast_cpu"}
    ):
        return FencedReceiptDecision("invalid_receipt")
    proof = verify_image_output_fence(
        session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        fencing_token=fencing_token, enabled=True, emergency_stop=False,
        now=now,
    )
    if proof.state != "fence_verified":
        return FencedReceiptDecision(proof.state)

    # verify_image_output_fence locked this exact ProcessingJob and slot.
    job = session.get(ProcessingJobModel, job_id)
    if job is None:  # defense in depth
        return FencedReceiptDecision("job_claim_not_verified")
    row = session.get(
        AgentImageOutputReceiptModel, (tenant_id, job_id),
        with_for_update=True, populate_existing=True,
    )
    if row is not None:
        if not _matches(
            row, job, agent_id=agent_id, fencing_token=fencing_token,
            output_sha256=output_sha256, output_bytes=output_bytes,
            source_sha256=source_sha256, engine=engine,
        ):
            return FencedReceiptDecision("receipt_conflict")
        return FencedReceiptDecision(
            "already_prepared" if row.status == "prepared" else "already_accepted",
            row.receipt_id, row.output_sha256, _location(row.receipt_id),
        )
    receipt_id = _receipt_identity(
        tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        claim_attempt_count=job.attempt_count,
        claim_started_at=job.claimed_at,
        fencing_token=fencing_token, output_sha256=output_sha256,
    )
    session.add(AgentImageOutputReceiptModel(
        tenant_id=tenant_id, job_id=job_id, agent_id=agent_id,
        claim_attempt_count=job.attempt_count,
        claim_started_at=_utc(job.claimed_at),
        fencing_token=fencing_token, receipt_id=receipt_id,
        output_sha256=output_sha256, output_bytes=output_bytes,
        source_sha256=source_sha256, engine=engine,
        status="prepared", prepared_at=_utc(now) or datetime.now(timezone.utc),
    ))
    session.flush()
    return FencedReceiptDecision(
        "prepared", receipt_id, output_sha256, _location(receipt_id),
    )


def _validate_png(data: bytes) -> bool:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FENCED_PNG_BYTES:
        return False
    if not data.startswith(_PNG_MAGIC):
        return False
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != "PNG" or img.width < 1 or img.height < 1:
                return False
            if img.width * img.height > 50_000_000:
                return False
            img.verify()
        # Verify compressed pixel stream rather than trusting only PNG headers.
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False
    return True


def _check_immutable(blob: Path, expected_hash: str, size: int) -> bool:
    """Read immutable content without following a replacement symlink."""
    fd = None
    try:
        fd = os.open(blob, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_FENCED_PNG_BYTES + 1)
        return len(data) == size and hashlib.sha256(data).hexdigest() == expected_hash
    except (OSError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _sync_directory(path: Path) -> None:
    """Persist the directory entry itself, not only its child file bytes."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_content_addressed(root: Path, receipt_id: str,
                             raw: bytes, expected_hash: str) -> bool:
    """Never overwrite a different blob; crash-safe orphan is recoverable."""
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        return False
    namespace = root / "fenced-v1"
    directory = namespace / receipt_id[:2]
    try:
        namespace.mkdir(mode=0o700, exist_ok=True)
        if namespace.is_symlink():
            return False
        _sync_directory(root)  # newly created namespace survives power loss
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            return False
        _sync_directory(namespace)  # newly created shard survives power loss
        target = directory / f"{receipt_id}.png"
        if target.exists() or target.is_symlink():
            if not _check_immutable(target, expected_hash, len(raw)):
                return False
            _sync_directory(directory)
            return True
        fd, pending = tempfile.mkstemp(prefix=".pending-", suffix=".png", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # link() creates the final name only if it is not already
                # present. os.replace() is deliberately forbidden here.
                os.link(pending, target)
            except FileExistsError:
                pass
            if not _check_immutable(target, expected_hash, len(raw)):
                return False
            _sync_directory(directory)
            return True
        finally:
            Path(pending).unlink(missing_ok=True)
    except (OSError, ValueError):
        return False


def accept_fenced_image_receipt(
    session: Session, *, tenant_id: str, agent_id: str, job_id: str,
    fencing_token: int, output_sha256: str, raw_png: bytes,
    storage_root: Path, source_sha256: str, engine: str,
    provenance_verified: bool = False, enabled: bool = False,
    emergency_stop: bool = True, now: datetime | None = None,
) -> FencedReceiptDecision:
    """Accept a prepared, physically fsynced PNG with its ProcessingJob.

    Caller MUST have performed existing orchestrator/source evidence checks,
    and MUST commit the Session. The filesystem write is intentionally *before*
    the DB commit: crash may leave an orphan, never a published DB reference
    pointing to a not-yet-written file. No live endpoint currently calls this.
    """
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return FencedReceiptDecision(blocked)
    if emergency_stop is not False:
        return FencedReceiptDecision("emergency_stop")
    if provenance_verified is not True:
        return FencedReceiptDecision("provenance_not_verified")
    if (
        not _valid_sha(output_sha256) or not _valid_sha(source_sha256)
        or not isinstance(engine, str)
        or engine not in {"chatgpt_assisted", "fast_cpu"}
        or not isinstance(raw_png, bytes)
        or not _valid_size(len(raw_png))
    ):
        return FencedReceiptDecision("invalid_receipt")
    if hashlib.sha256(raw_png).hexdigest() != output_sha256 or not _validate_png(raw_png):
        return FencedReceiptDecision("artifact_integrity_error")

    clock = _utc(now) or datetime.now(timezone.utc)
    # Lock ProcessingJob first. Even completed retries are serialized against
    # a prior accepted result, which can be replayed only as a read-only ACK.
    job = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job_id,
        ProcessingJobModel.tenant_id == tenant_id,
    ).with_for_update())
    if job is None:
        return FencedReceiptDecision("job_claim_not_verified")
    if type(fencing_token) is not int or fencing_token <= 0:
        return FencedReceiptDecision("stale_fencing_token")
    if job.status == "completed":
        if not _active_image_agent(session, tenant_id, agent_id):
            return FencedReceiptDecision("device_not_verified")
        slot = session.get(
            AgentExecutorSlotModel, (tenant_id, agent_id),
            with_for_update=True, populate_existing=True,
        )
        row = session.get(
            AgentImageOutputReceiptModel, (tenant_id, job_id),
            with_for_update=True, populate_existing=True,
        )
        prior = (job.payload_json or {}).get("result")
        if (
            slot is None or slot.fencing_token != fencing_token
            or row is None or row.status != "accepted"
            or job.claimed_at is None or job.attempt_count < 1
            or not _matches(
                row, job, agent_id=agent_id, fencing_token=fencing_token,
                output_sha256=output_sha256, output_bytes=len(raw_png),
                source_sha256=source_sha256, engine=engine,
            )
            or not isinstance(prior, dict)
            or prior.get("receipt_id") != row.receipt_id
            or prior.get("output_sha256") != row.output_sha256
            or prior.get("source_sha256") != source_sha256
            or prior.get("engine") != engine
        ):
            return FencedReceiptDecision("stale_or_conflicting_replay")
        # A completed DB receipt is not proof of present bytes after a disk
        # incident. Never acknowledge a missing/corrupted immutable artifact.
        if (
            not isinstance(storage_root, Path) or not storage_root.is_absolute()
            or storage_root.is_symlink() or not storage_root.is_dir()
            or not _check_immutable(
                storage_root / _location(row.receipt_id),
                row.output_sha256, row.output_bytes,
            )
        ):
            return FencedReceiptDecision("storage_not_verified")
        return FencedReceiptDecision(
            "accepted_replay", row.receipt_id, row.output_sha256,
            _location(row.receipt_id),
        )

    proof = verify_image_output_fence(
        session, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id,
        fencing_token=fencing_token, enabled=True, emergency_stop=False,
        now=clock,
    )
    if proof.state != "fence_verified":
        return FencedReceiptDecision(proof.state)
    row = session.get(
        AgentImageOutputReceiptModel, (tenant_id, job_id),
        with_for_update=True, populate_existing=True,
    )
    if row is None:
        return FencedReceiptDecision("receipt_not_prepared")
    if not _matches(
        row, job, agent_id=agent_id, fencing_token=fencing_token,
        output_sha256=output_sha256, output_bytes=len(raw_png),
        source_sha256=source_sha256, engine=engine,
    ):
        return FencedReceiptDecision("receipt_conflict")
    if row.status != "prepared":
        return FencedReceiptDecision("receipt_not_prepared")
    if not isinstance(storage_root, Path) or not _stage_content_addressed(
        storage_root, row.receipt_id, raw_png, output_sha256,
    ):
        return FencedReceiptDecision("storage_not_verified")
    result = {
        "status": "needs_review",
        "engine": engine,
        "source_sha256": source_sha256,
        "output_sha256": output_sha256,
        "receipt_id": row.receipt_id,
        "fenced_storage_ref": _location(row.receipt_id),
        "manual_qa_required": True,
        "print_ready": False,
        "rights_status": "unverified",
        "finished_at": clock.isoformat(),
        "claim_attempt_count": job.attempt_count,
        "fencing_token": fencing_token,
    }
    job.payload_json = {**(job.payload_json or {}), "result": result}
    job.status = "completed"
    job.completed_at = clock
    job.lease_expires_at = None
    row.status = "accepted"
    row.accepted_at = clock
    session.flush()
    return FencedReceiptDecision(
        "accepted", row.receipt_id, row.output_sha256,
        _location(row.receipt_id),
    )
