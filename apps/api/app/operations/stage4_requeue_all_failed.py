"""Operator-triggered bulk restore of failed Stage 4 jobs to the durable queue.

Run from the production API environment with --apply; defaults to dry run.
Avoid resetting already published output or jobs with active processing rows.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import STAGE2_JOB_TYPE


def recover_all_failed(*, apply: bool = False, batch_size: int = 25) -> dict[str, int]:
    result = {"candidates": 0, "requeued": 0, "skipped_published": 0,
              "skipped_active": 0, "errors": 0}
    cursor: str | None = None
    settings = get_settings()
    while True:
        with SessionLocal() as session:
            query = (select(RrugcStage2JobModel.id)
                     .where(RrugcStage2JobModel.status == "failed")
                     .order_by(RrugcStage2JobModel.id)
                     .limit(batch_size))
            if cursor:
                query = query.where(RrugcStage2JobModel.id > cursor)
            ids = list(session.scalars(query))
        if not ids:
            break
        cursor = ids[-1]
        for job_id in ids:
            with SessionLocal() as session:
                try:
                    row = session.scalar(select(RrugcStage2JobModel).where(
                        RrugcStage2JobModel.id == job_id,
                        RrugcStage2JobModel.status == "failed",
                    ).with_for_update(skip_locked=True))
                    if row is None:
                        continue
                    result["candidates"] += 1
                    if row.output_remote_file_id:
                        result["skipped_published"] += 1
                        continue
                    prior = session.get(ProcessingJobModel, row.processing_job_id) if row.processing_job_id else None
                    if prior is not None and prior.status not in ("failed", "cancelled", "completed"):
                        result["skipped_active"] += 1
                        continue
                    if not apply:
                        continue
                    now = datetime.now(timezone.utc)
                    # Keep old processing jobs and failure details as durable history.
                    next_job = ProcessingRepository(session, settings).create_job(
                        tenant_id=row.tenant_id,
                        job_type=STAGE2_JOB_TYPE,
                        entity_type="rrugc_stage2_job",
                        entity_id=row.id,
                        idempotency_key=f"rrugc-stage2:{row.id}:bulk-restore:{uuid4()}",
                        payload={"stage2_job_id": row.id},
                        priority=25,
                        max_attempts=3,
                        next_attempt_at=now,
                        provider_key="codex",
                        provider_scope="ai",
                    )
                    row.processing_job_id = next_job.id
                    row.status = "queued"
                    row.queued_at = now
                    row.started_at = None
                    session.commit()
                    result["requeued"] += 1
                except Exception:
                    session.rollback()
                    result["errors"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("batch-size must be 1..100")
    print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                      **recover_all_failed(apply=args.apply, batch_size=args.batch_size)}))


if __name__ == "__main__":
    main()
