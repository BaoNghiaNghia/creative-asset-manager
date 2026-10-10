"""One-time operator repair of three Stage 1 failed final-set jobs.

Explicit --apply only; never retries all failed jobs. Preserves old
ProcessingJob history and original output-version rows; queues a new
one-attempt processing job for each of the three exact screenshot cases.
"""
from __future__ import annotations
import argparse
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcKeywordImageJobModel, RrugcImageOutputVersionModel
from app.modules.realistic_review_ugc.keyword_images import KeywordImageService
from app.modules.auth_persistence.model import AuthAuditEventModel

CASES = {
    "7a05f5d1-82f8-4567-9806-207c7f1305d6": "TAILGATE CAPTAIN",
    "cc71d945-ff93-4377-a6c8-b37514547298": "Run the dang ball",
    "ccb482a0-a886-4048-80a0-37822b4816ae": "FOOTBALL Mom",
}

def inspect(session, *, lock: bool = False):
    rows = []
    for job_id, keyword in CASES.items():
        stmt=select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.id == job_id,
            RrugcKeywordImageJobModel.keyword_text == keyword,
        )
        if lock:
            stmt=stmt.with_for_update()
        row=session.scalar(stmt)
        if row is None or row.status != "failed" or row.last_error_code != "stage1_six_outputs_invalid":
            raise RuntimeError(f"{keyword}: failed-job identity/state changed; aborting")
        processing=session.get(ProcessingJobModel,row.processing_job_id)
        if processing is None or processing.status != "failed" or processing.attempt_count != 1:
            raise RuntimeError(f"{keyword}: old attempt must be terminal (once-only)")
        count=int(session.scalar(select(func.count()).select_from(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.job_id==job_id,
            RrugcImageOutputVersionModel.tenant_id==row.tenant_id,
            RrugcImageOutputVersionModel.stage=="stage1")) or 0)
        if count or row.output_remote_file_id:
            raise RuntimeError(f"{keyword}: already has saved finals; manual review required")
        rows.append(row)
    if len({r.tenant_id for r in rows}) != 1:
        raise RuntimeError("Targets span multiple tenants")
    return rows

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply",action="store_true")
    args=ap.parse_args()
    with SessionLocal() as session:
        rows=inspect(session,lock=args.apply)
        print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(rows)} failed keyword jobs eligible for safe one-time operator repair")
        for row in rows: print(f"  {row.keyword_text}: {row.id} old_process={row.processing_job_id}")
        if not args.apply:
            return
        service=KeywordImageService(session)
        for row in rows:
            old_process=row.processing_job_id
            row.status="queued"
            row.last_error_code=None
            row.last_error_message=None
            row.started_at=None
            row.completed_at=None
            row.retry_count=0
            service._enqueue(row)
            session.add(AuthAuditEventModel(
                tenant_id=row.tenant_id,
                actor_id="operator-stage1-final-repair",
                action="rrugc.keyword_image.operator_requeued",
                detail_json={"keyword_image_job_id":row.id,
                             "old_processing_job_id":old_process,
                             "new_processing_job_id":row.processing_job_id,
                             "reason":"six_final_output_contract_repair"},
            ))
        session.commit()
        print("SUCCESS: three new one-attempt processing jobs queued; old attempt logs retained.")

if __name__=="__main__":main()
