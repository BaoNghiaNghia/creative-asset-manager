"""Conservative, bounded reconciliation for Stage 4 generation statuses.

A run must never remain "Queued" after the referenced processing job has
terminally failed. Previously published output versions are never deleted.
One additional processing run is permitted for legacy exhausted image-output
failures; subsequent failures remain visible for operator review.
"""
from __future__ import annotations

import logging

from sqlalchemy import and_, func, or_, select

from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import RrugcStage2Service

LOGGER = logging.getLogger(__name__)
AUTO_REPLAY_ERRORS = frozenset({"codex_image_output_missing", "codex_account_pool_busy", "managed_storage_unavailable", "stage2_generation_internal_error", "TimeoutError", "ConnectionError", "codex_image_generation_failed", "gemini_model_pool_temporarily_unavailable"})

# Limit automated recovery to three extra processing runs per image. Permanent
# validation/source/skill failures never qualify; repeated failures stay visible.
MAX_AUTO_PROCESSING_RUNS = 4



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
    report = {"eligible": 0, "failed_synced": 0, "auto_requeued": 0, "skipped": 0}
    with SessionLocal() as session:
        candidates = list(session.execute(
            select(RrugcStage2JobModel.id, RrugcStage2JobModel.tenant_id)
            .where(or_(
                RrugcStage2JobModel.status.in_(("queued", "running")),
                and_(RrugcStage2JobModel.status == "failed",
                     RrugcStage2JobModel.last_error_code.in_(AUTO_REPLAY_ERRORS)),
            ))
            .order_by(RrugcStage2JobModel.queued_at.desc())
            .limit(max(1, min(limit * 8, 200)))
        ))
    for job_id, tenant_id in candidates:
        if report["eligible"] >= limit:
            break
        with SessionLocal() as session:
            row = session.scalar(
                select(RrugcStage2JobModel)
                .where(
                    RrugcStage2JobModel.id == job_id,
                    RrugcStage2JobModel.tenant_id == tenant_id,
                    or_(RrugcStage2JobModel.status.in_(("queued", "running")),
                        and_(RrugcStage2JobModel.status == "failed",
                             RrugcStage2JobModel.last_error_code.in_(AUTO_REPLAY_ERRORS))),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            processing = session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
            if processing is not None and processing.status not in ("failed", "completed"):
                continue
            if row.status == "failed" and (processing is None or processing.status != "failed"):
                continue
            if processing is not None and processing.status == "completed":
                # Do not infer successful output from processing status alone.
                report["skipped"] += 1
                continue
            if row.output_remote_file_id:
                # Published images win; never overwrite or reset their status.
                report["skipped"] += 1
                continue
            report["eligible"] += 1
            if dry_run:
                continue
            error_code = processing.last_error_code if processing else "stage4_processing_job_missing"
            was_failed = row.status == "failed"
            row.status = "failed"
            row.last_error_code = error_code or "stage4_processing_failed"
            row.last_error_message = (
                "Generation stopped without an output. The processing job exhausted its attempts. "
                "Use Retry after checking the Skill logs."
            )
            session.commit()
            if processing is not None and not was_failed:
                report["failed_synced"] += 1

            if not processing or error_code not in AUTO_REPLAY_ERRORS:
                continue
            # Recover transient failures only while the bounded retry budget
            # remains. The service creates a fresh durable processing job.
            total = int(session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "rrugc_stage2_generate",
                ProcessingJobModel.entity_type == "rrugc_stage2_job",
                ProcessingJobModel.entity_id == job_id,
            )) or 0)
            if total >= MAX_AUTO_PROCESSING_RUNS or processing.attempt_count < processing.max_attempts:
                continue
            try:
                RrugcStage2Service(session).retry_failed_job(
                    tenant_id=tenant_id, user_id=row.created_by_user_id, job_id=job_id,
                )
                report["auto_requeued"] += 1
                LOGGER.warning("stage4_terminal_auto_requeued job_id=%s", job_id)
            except Exception as exc:
                session.rollback()
                LOGGER.warning(
                    "stage4_terminal_auto_requeue_blocked job_id=%s error_type=%s",
                    job_id, type(exc).__name__,
                )
    return report
