"""Durable Stage 4 queue recovery without a fixed retry ceiling.

Unavailable Codex sessions leave work queued. Previously published output
versions are never deleted; cancelled/finished runs are never replayed.
"""
from __future__ import annotations

import logging

from sqlalchemy import and_, or_, select

from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import RrugcStage2Service

LOGGER = logging.getLogger(__name__)

def replay_legacy_attribute_error(*, limit: int = 2, dry_run: bool = False) -> dict[str, int]:
    """One-time replay for two-step chains spoiled by a past ClaimedJob regression.

    Qualifies only if the ORIGINAL processing run exhausted on
    codex_image_output_missing, its ONE automatic replay exhausted on the
    AttributeError produced by the previous runtime, and no output exists.
    This will never requeue a third time or modify published versions.
    """
    result = {"eligible": 0, "requeued": 0, "blocked": 0}
    with SessionLocal() as session:
        candidates = list(session.execute(
            select(RrugcStage2JobModel.id, RrugcStage2JobModel.tenant_id)
            .where(
                RrugcStage2JobModel.status == "failed",
                RrugcStage2JobModel.last_error_code == "AttributeError",
                RrugcStage2JobModel.output_remote_file_id.is_(None),
            )
            .order_by(RrugcStage2JobModel.updated_at.asc())
            .limit(max(1, min(limit * 6, 100)))
        ))
    for job_id, tenant_id in candidates:
        if result["eligible"] >= limit:
            break
        with SessionLocal() as session:
            row = session.scalar(select(RrugcStage2JobModel).where(
                RrugcStage2JobModel.id == job_id,
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.status == "failed",
                RrugcStage2JobModel.last_error_code == "AttributeError",
                RrugcStage2JobModel.output_remote_file_id.is_(None),
            ).with_for_update(skip_locked=True))
            if row is None:
                continue
            history = list(session.scalars(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "rrugc_stage2_generate",
                ProcessingJobModel.entity_type == "rrugc_stage2_job",
                ProcessingJobModel.entity_id == job_id,
            ).order_by(ProcessingJobModel.created_at.asc())))
            if (
                len(history) != 2
                or history[0].last_error_code != "codex_image_output_missing"
                or history[1].last_error_code != "AttributeError"
                or history[1].status != "failed"
                or history[1].id != row.processing_job_id
                or history[1].attempt_count < history[1].max_attempts
            ):
                continue
            result["eligible"] += 1
            if dry_run:
                continue
            try:
                RrugcStage2Service(session).retry_failed_job(
                    tenant_id=tenant_id, user_id=row.created_by_user_id, job_id=job_id,
                )
                result["requeued"] += 1
                LOGGER.warning("stage4_runtime_regression_replayed_once job_id=%s", job_id)
            except Exception as exc:
                session.rollback()
                result["blocked"] += 1
                LOGGER.warning(
                    "stage4_runtime_regression_replay_blocked job_id=%s error_type=%s",
                    job_id, type(exc).__name__,
                )
    return result

def reconcile_stage4_terminal(*, limit: int = 12, dry_run: bool = False) -> dict[str, int]:
    """Revive every unfinished Stage 4 job, regardless of prior retry count.

    Reuse the existing durable processing record instead of creating an
    unbounded history of exhausted jobs. Row-level locks serialize recovery,
    and only FAILED processing records are reset. Completed/cancelled work
    and published output are never modified.
    """
    from datetime import datetime, timedelta, timezone
    from app.modules.processing.repository import ProcessingRepository

    report = {"eligible": 0, "failed_synced": 0, "auto_requeued": 0, "skipped": 0}
    with SessionLocal() as session:
        candidates = list(session.execute(
            select(RrugcStage2JobModel.id, RrugcStage2JobModel.tenant_id)
            .outerjoin(
                ProcessingJobModel,
                ProcessingJobModel.id == RrugcStage2JobModel.processing_job_id,
            )
            .where(
                RrugcStage2JobModel.output_remote_file_id.is_(None),
                or_(
                    and_(
                        RrugcStage2JobModel.status == "failed",
                        or_(
                            ProcessingJobModel.status.in_(("failed", "pending", "retry", "processing")),
                            ProcessingJobModel.id.is_(None),
                        ),
                    ),
                    and_(
                        RrugcStage2JobModel.status.in_(("queued", "running")),
                        or_(ProcessingJobModel.status == "failed", ProcessingJobModel.id.is_(None)),
                    ),
                ),
            )
            .order_by(RrugcStage2JobModel.queued_at.asc(), RrugcStage2JobModel.id.asc())
            .limit(max(1, min(limit, 1000)))
        ))
    for job_id, tenant_id in candidates:
        with SessionLocal() as session:
            row = session.scalar(select(RrugcStage2JobModel).where(
                RrugcStage2JobModel.id == job_id,
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.status.in_(("queued", "running", "failed")),
                RrugcStage2JobModel.output_remote_file_id.is_(None),
            ).with_for_update(skip_locked=True))
            if row is None:
                continue
            processing = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == row.processing_job_id,
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "rrugc_stage2_generate",
                ProcessingJobModel.entity_id == row.id,
            ).with_for_update(skip_locked=True)) if row.processing_job_id else None
            if processing is not None and (
                processing.status == "completed" or processing.cancellation_requested
            ):
                report["skipped"] += 1
                continue
            if processing is not None and processing.status not in ("failed", "pending", "retry", "processing"):
                report["skipped"] += 1
                continue
            if processing is not None and processing.status in ("pending", "retry", "processing"):
                if row.status == "failed":
                    report["eligible"] += 1
                    if not dry_run:
                        row.status = "running" if processing.status == "processing" else "queued"
                        session.commit()
                        report["failed_synced"] += 1
                continue
            report["eligible"] += 1
            if dry_run:
                continue
            now = datetime.now(timezone.utc)
            if processing is None:
                processing = ProcessingRepository(session).create_job(
                    tenant_id=tenant_id,
                    job_type="rrugc_stage2_generate",
                    entity_type="rrugc_stage2_job",
                    entity_id=row.id,
                    idempotency_key=f"rrugc-stage4-recover:{row.id}:{now.timestamp()}",
                    payload={"stage2_job_id": row.id},
                    priority=25, max_attempts=3,
                    next_attempt_at=now + timedelta(seconds=60),
                    provider_key="codex", provider_scope="ai",
                )
                row.processing_job_id = processing.id
            else:
                # A terminal record has no owner; reset the retry counter so
                # a later Codex recovery can claim it without a fixed ceiling.
                processing.status = "pending"
                processing.attempt_count = 0
                processing.claimed_by = None
                processing.claimed_at = None
                processing.lease_expires_at = None
                processing.completed_at = None
                processing.next_attempt_at = now + timedelta(seconds=60)
                processing.updated_at = now
            row.status = "queued"
            row.started_at = None
            row.queued_at = now
            # Keep error code/message visible to explain why it is waiting.
            session.commit()
            report["auto_requeued"] += 1
            LOGGER.warning("stage4_failed_job_restored_to_queue job_id=%s", job_id)
    return report