"""Conservative, bounded reconciliation for Stage 4 generation statuses.

A run must never remain "Queued" after the referenced processing job has
terminally failed. Previously published output versions are never deleted.
One additional processing run is permitted for legacy exhausted image-output
failures; subsequent failures remain visible for operator review.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import RrugcStage2Service

LOGGER = logging.getLogger(__name__)
AUTO_REPLAY_ERRORS = frozenset({"codex_image_output_missing"})


def reconcile_stage4_terminal(*, limit: int = 12, dry_run: bool = False) -> dict[str, int]:
    report = {"eligible": 0, "failed_synced": 0, "auto_requeued": 0, "skipped": 0}
    with SessionLocal() as session:
        candidates = list(session.execute(
            select(RrugcStage2JobModel.id, RrugcStage2JobModel.tenant_id)
            .where(RrugcStage2JobModel.status.in_(("queued", "running")))
            .order_by(RrugcStage2JobModel.queued_at.asc())
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
                    RrugcStage2JobModel.status.in_(("queued", "running")),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            processing = session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
            if processing is not None and processing.status not in ("failed", "completed"):
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
            row.status = "failed"
            row.last_error_code = error_code or "stage4_processing_failed"
            row.last_error_message = (
                "Generation stopped without an output. The processing job exhausted its attempts. "
                "Use Retry after checking the Skill logs."
            )
            session.commit()
            report["failed_synced"] += 1

            if not processing or error_code not in AUTO_REPLAY_ERRORS:
                continue
            # Only original runs qualify. Once the first automatic replay
            # exists it cannot recursively trigger another replay.
            total = int(session.scalar(select(func.count()).select_from(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "rrugc_stage2_generate",
                ProcessingJobModel.entity_type == "rrugc_stage2_job",
                ProcessingJobModel.entity_id == job_id,
            )) or 0)
            if total != 1 or processing.attempt_count < processing.max_attempts:
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
