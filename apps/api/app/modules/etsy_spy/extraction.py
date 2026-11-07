"""Etsy Picked -> isolated extraction jobs, never claimed by Codex CLI workers.

The Windows Image Agent transport is intentionally separate from normal CAM
processing workers. No existing IMAGE_GENERATE job or provider selection changes.
"""
from __future__ import annotations

from hashlib import sha256
import os

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.processing.types import JOB_TYPES
from app.modules.processing.model import ProcessingJobModel
from .model import EtsySpyListing
from .service import verified_image_url

EXTRACTION_JOB_TYPE = "etsy_design_extract"
EXTRACTION_ENGINE = "windows_image_agent"
EXTRACTION_VERSION = "fast-cpu-v1"
assert EXTRACTION_JOB_TYPE not in JOB_TYPES, "External-only jobs must not be claimed by CAM/Codex workers"


def extraction_enabled() -> bool:
    # Do not build a production backlog until transport, storage and the paired
    # Windows agent have passed the isolated one-slot live pilot.
    from pathlib import Path
    storage = os.getenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", "").strip()
    return (
        os.getenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "0") == "1"
        and bool(storage) and Path(storage).is_absolute()
    )


def create_extraction_on_pick(db: Session, tenant_id: str, listing: EtsySpyListing) -> ProcessingJobModel | None:
    """Create one immutable source-version task in the caller's Pick transaction.

    Source URLs are metadata only; this code does not fetch Etsy or render assets.
    Every result needs manual QA and rights review before any commercial use.
    """
    if not extraction_enabled():
        return None
    metadata = listing.metadata_json if isinstance(listing.metadata_json, dict) else {}
    source_url = verified_image_url(metadata.get("fullImageUrl")) or verified_image_url(
        metadata.get("thumbnailUrl"))
    if not source_url:
        return None
    source_revision = sha256(source_url.encode("utf-8")).hexdigest()
    key = f"etsy-extract:{EXTRACTION_VERSION}:{listing.listing_key}:{source_revision}"
    previous = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.idempotency_key == key,
    ))
    if previous is not None:
        if previous.status in {"pending", "retry", "processing"} and previous.cancellation_requested:
            previous.cancellation_requested = False
        return previous
    payload = {
        "schema": "cam-etsy-extraction-v1",
        "listing_key": listing.listing_key,
        "listing_title": listing.title[:500],
        "source": {"type": "etsy_cdn", "url": source_url},
        "source_url_sha256": source_revision,
        "engine": "auto",
        "fallback": "ai_review",
        "rights_status": "unverified",
        "print_ready": False,
        "manual_qa_required": True,
    }
    job = ProcessingJobModel(
        tenant_id=tenant_id,
        job_type=EXTRACTION_JOB_TYPE,
        entity_type="etsy_listing",
        entity_id=listing.listing_key,
        idempotency_key=key,
        payload_json=payload,
        provider_key=EXTRACTION_ENGINE,
        provider_scope="external",
        priority=20,
        max_attempts=3,
        status="pending",
    )
    # Savepoint preserves the outer Pick transaction even during a duplicate
    # request raced by another web process.
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        prior = db.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.idempotency_key == key,
        ))
        if prior is None:
            raise
        return prior
    return job


def latest_extraction_job(db: Session, tenant_id: str, listing_key: str) -> ProcessingJobModel | None:
    return db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.entity_type == "etsy_listing",
        ProcessingJobModel.entity_id == listing_key,
    ).order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc()).limit(1))


def cancel_pending_extractions(db: Session, tenant_id: str, listing_key: str) -> None:
    for job in db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.entity_id == listing_key,
        ProcessingJobModel.status.in_(("pending", "retry", "processing")),
    )):
        job.cancellation_requested = True
