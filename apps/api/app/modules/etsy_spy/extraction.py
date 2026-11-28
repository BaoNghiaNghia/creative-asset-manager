"""Etsy Picked -> isolated extraction jobs, never claimed by Codex CLI workers.

The Windows Image Agent transport is intentionally separate from normal CAM
processing workers. No existing IMAGE_GENERATE job or provider selection changes.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone
from uuid import uuid4
import os

from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.processing.types import JOB_TYPES
from app.modules.processing.model import ProcessingJobModel
from .model import EtsySpyListing, EtsySpyPicked
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


ELIGIBLE_SOURCE = "eligible"


def eligible_extraction_enabled() -> bool:
    """Independent rollout flag; existing Picked processing remains intact."""
    return extraction_enabled() and os.getenv("CAM_ETSY_ELIGIBLE_EXTRACTION_ENABLED", "1") == "1"


def lock_extraction_queue(db: Session, tenant: str) -> None:
    """Use the SAME transaction lock as Image Agent claims and Pick creation."""
    if db.get_bind().dialect.name == "postgresql":
        digest = sha256(("cam:image-claim:v1:" + tenant).encode()).digest()
        db.scalar(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True))))


def eligible_listing_conditions(tenant: str):
    # Import lazily to avoid the router/extraction module cycle. Keep discovery,
    # season routing, blacklist and demand filters identical to Eligible UI.
    from .router import _listing_state_conditions
    return _listing_state_conditions(tenant, "eligible")


def extraction_access_condition(tenant: str, job=ProcessingJobModel):
    picked = select(EtsySpyPicked.listing_key).where(
        EtsySpyPicked.tenant_id == tenant, EtsySpyPicked.listing_key == job.entity_id
    ).correlate(job).exists()
    eligible = select(EtsySpyListing.listing_key).where(
        *eligible_listing_conditions(tenant), EtsySpyListing.listing_key == job.entity_id
    ).correlate(job).exists()
    return or_(picked, and_(
        or_(eligible_extraction_enabled(), job.payload_json["source_scope"].as_string() == ELIGIBLE_SOURCE),
        eligible,
    ))


def extraction_job_allowed(db: Session, tenant: str, job: ProcessingJobModel) -> bool:
    if job.tenant_id != tenant or job.job_type != EXTRACTION_JOB_TYPE:
        return False
    return db.scalar(select(ProcessingJobModel.id).where(
        ProcessingJobModel.id == job.id,
        ProcessingJobModel.tenant_id == tenant,
        extraction_access_condition(tenant),
    )) is not None


def missing_eligible_query(tenant: str):
    existing = select(ProcessingJobModel.id).where(
        ProcessingJobModel.tenant_id == tenant,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.entity_type == "etsy_listing",
        ProcessingJobModel.entity_id == EtsySpyListing.listing_key,
        ProcessingJobModel.cancellation_requested.is_(False),
    ).correlate(EtsySpyListing).exists()
    return select(EtsySpyListing).where(*eligible_listing_conditions(tenant), ~existing)


def eligible_available_count(db: Session, tenant: str) -> int:
    if not eligible_extraction_enabled():
        return 0
    q = missing_eligible_query(tenant).with_only_columns(EtsySpyListing.listing_key).distinct()
    return int(db.scalar(select(func.count()).select_from(q.subquery())) or 0)


def enqueue_next_eligible(db: Session, tenant: str) -> ProcessingJobModel | None:
    """Lazy bounded refill after the normal Picked queue is empty. No Pick side effects."""
    if not eligible_extraction_enabled():
        return None
    lock_extraction_queue(db, tenant)
    rows = db.scalars(missing_eligible_query(tenant).order_by(
        EtsySpyListing.demand_score.desc().nulls_last(),
        EtsySpyListing.observed_at.desc().nulls_last(),
        EtsySpyListing.listing_key,
    ).limit(500)).all()
    for listing in rows:
        meta = listing.metadata_json if isinstance(listing.metadata_json, dict) else {}
        source_url = verified_image_url(meta.get("fullImageUrl")) or verified_image_url(meta.get("thumbnailUrl"))
        if not source_url:
            continue
        return create_extraction_on_pick(db, tenant, listing, datetime.now(timezone.utc), source_scope=ELIGIBLE_SOURCE)
    return None


def create_extraction_on_pick(
    db: Session, tenant_id: str, listing: EtsySpyListing, picked_at: datetime,
    *, source_scope: str = "picked",
) -> ProcessingJobModel | None:
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
    lock_extraction_queue(db, tenant_id)
    # A design already queued/saved from Eligible survives Pick. An in-flight
    # job cannot be duplicated even if a newer Scout observation arrives.
    existing = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
        ProcessingJobModel.entity_id == listing.listing_key,
        ProcessingJobModel.cancellation_requested.is_(False),
        ProcessingJobModel.payload_json["source_scope"].as_string() == ELIGIBLE_SOURCE,
        or_(ProcessingJobModel.status.in_(("pending", "retry", "processing")),
            ProcessingJobModel.payload_json["source_url_sha256"].as_string() == source_revision),
    ).order_by(ProcessingJobModel.created_at.desc()).limit(1))
    if existing is not None:
        if source_scope == "picked" and existing.status in ("pending", "retry"):
            existing.priority = max(existing.priority, 20)
        return existing
    # Pick after Unpick starts a *new* generation. Reusing a cancelled
    # processing row would otherwise make the old worker's lease valid again.
    # Canonicalize SQLite-naive picked_at and PostgreSQL-aware picked_at to UTC.
    pick_utc = (picked_at.replace(tzinfo=timezone.utc) if picked_at.tzinfo is None
                else picked_at.astimezone(timezone.utc))
    generation = sha256(pick_utc.isoformat(timespec="microseconds").encode()).hexdigest()[:24]
    key = f"etsy-extract:{EXTRACTION_VERSION}:{listing.listing_key}:{source_revision}:{generation}"
    previous = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.idempotency_key == key,
    ))
    if previous is not None:
        # Never revive a cancelled generation: its lease might still exist
        # on an offline worker, and revival would accept a stale PNG.
        return previous
    payload = {
        "schema": "cam-etsy-extraction-v1",
        "listing_key": listing.listing_key,
        "listing_title": listing.title[:500],
        "source": {"type": "etsy_cdn", "url": source_url},
        "source_url_sha256": source_revision,
        "pick_generation": generation,
        "source_scope": source_scope,
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
        priority=10 if source_scope == ELIGIBLE_SOURCE else 20,
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


def create_extraction_retry(
    db: Session, tenant_id: str, listing_key: str,
    source_job: ProcessingJobModel, selected_asset: dict,
    source_url: str,
) -> ProcessingJobModel:
    """Queue a new immutable generation through the SAME current Image Agent.

    No old output, worker lease, or commercial QA status is overwritten.
    The selected asset is provenance for this listing-level re-extraction;
    the active Orchestrator decides how many positions the source contains.
    The caller locks the Picked row and checks active jobs before calling.
    """
    source_hash = sha256(source_url.encode("utf-8")).hexdigest()
    key = (f"etsy-extract:{EXTRACTION_VERSION}:manual-retry:"
           f"{listing_key}:{uuid4().hex}")
    payload = {
        "schema": "cam-etsy-extraction-v1",
        "listing_key": listing_key,
        "listing_title": str(source_job.payload_json.get("listing_title") or "")[:500],
        "source": {"type": "etsy_cdn", "url": source_url},
        "source_url_sha256": source_hash,
        "pick_generation": uuid4().hex[:24],
        "source_scope": source_job.payload_json.get("source_scope", "picked"),
        "engine": "auto",
        "fallback": "ai_review",
        "rights_status": "unverified",
        "print_ready": False,
        "manual_qa_required": True,
        "retry": {
            "source_job_id": source_job.id,
            "asset_id": selected_asset["id"],
            "output_sha256": selected_asset["output_sha256"],
            "position": selected_asset["position"],
            "variant": selected_asset["variant"],
        },
    }
    job = ProcessingJobModel(
        tenant_id=tenant_id, job_type=EXTRACTION_JOB_TYPE,
        entity_type="etsy_listing", entity_id=listing_key,
        idempotency_key=key, payload_json=payload,
        provider_key=EXTRACTION_ENGINE, provider_scope="external",
        priority=30, max_attempts=3, status="pending",
    )
    db.add(job)
    db.flush()
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
        # Eligible-origin jobs remain useful if the user simply unpicks them.
        if (eligible_extraction_enabled() and
            (job.payload_json or {}).get("source_scope") == ELIGIBLE_SOURCE and
            extraction_job_allowed(db, tenant_id, job)):
            continue
        job.cancellation_requested = True
