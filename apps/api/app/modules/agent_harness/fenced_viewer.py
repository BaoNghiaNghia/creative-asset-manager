"""Inert Phase 4C.3F read-only adapter for immutable accepted PNG receipts.

Returns *verified bytes*, never a mutable path, unverified FileResponse or
an inferred print/right clearance. Future route must authenticate the user's
active tenant before invoking, and keep this Session open through validation.
Nothing in current CAM routes imports this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from .executor_slots import _preflight
from .fenced_reconciliation import classify_fenced_receipt_snapshot
from .fenced_storage import read_fenced_blob_pinned
from .model import AgentImageOutputReceiptModel


@dataclass(frozen=True)
class VerifiedImageRead:
    state: str
    png: bytes | None = None
    output_sha256: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "verified_review_only" and self.png is not None


def read_accepted_image_for_review(
    session: Session, *, tenant_id: str, job_id: str,
    storage_root: Path | None = None,
    enabled: bool = False, emergency_stop: bool = True,
) -> VerifiedImageRead:
    blocked = _preflight(session, enabled=enabled)
    if blocked:
        return VerifiedImageRead(blocked)
    if emergency_stop is not False:
        return VerifiedImageRead("emergency_stop")

    # Read locks give stable DB metadata during the descriptor-pinned byte read.
    job = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.id == job_id,
        ProcessingJobModel.job_type == "etsy_design_extract",
    ).with_for_update(read=True))
    if job is None:
        return VerifiedImageRead("not_found")
    row = session.scalar(select(AgentImageOutputReceiptModel).where(
        AgentImageOutputReceiptModel.tenant_id == tenant_id,
        AgentImageOutputReceiptModel.job_id == job_id,
    ).with_for_update(read=True))
    if row is None or row.status != "accepted":
        return VerifiedImageRead("not_available")
    # classify checks the authoritative DB payload against the receipt, while
    # its disk check remains only a diagnostic. The pinned read below is the
    # definitive byte proof; never return the path from the audit result.
    snapshot = classify_fenced_receipt_snapshot(
        tenant_id=tenant_id, job=job, row=row, slot=None, agent=None,
        storage_root=storage_root,
    )
    if snapshot.state != "accepted_verified":
        return VerifiedImageRead("not_verified")
    raw = read_fenced_blob_pinned(
        storage_root, namespace="fenced-v1",
        key=row.receipt_id, expected_sha256=row.output_sha256,
        expected_bytes=row.output_bytes,
    )
    if raw is None:
        return VerifiedImageRead("not_verified")
    return VerifiedImageRead(
        "verified_review_only", png=raw, output_sha256=row.output_sha256,
    )
