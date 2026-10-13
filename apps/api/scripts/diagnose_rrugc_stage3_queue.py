"""Read-only RRUGC Stage 3 queue diagnostic; no credentials or raw prompts are printed."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel

now=datetime.now(timezone.utc)
with SessionLocal() as db:
    print("QUEUE_COUNTS")
    relevant=("rrugc_source_plan_analyze", "rrugc_candidate_analyze", "rrugc_stage3_analyze", "asset_analyze", "rrugc_candidate_import")
    for job_type, status, count, priority in db.execute(
        select(ProcessingJobModel.job_type,ProcessingJobModel.status,func.count(),func.min(ProcessingJobModel.priority))
        .where(ProcessingJobModel.job_type.in_(relevant))
        .group_by(ProcessingJobModel.job_type,ProcessingJobModel.status)
        .order_by(ProcessingJobModel.job_type,ProcessingJobModel.status)
    ):
        print(job_type, status, count, "min_priority", priority)
    print("PLAN_COUNTS")
    for status,count in db.execute(select(RrugcSourcePlanModel.status,func.count()).group_by(RrugcSourcePlanModel.status)):
        print(status,count)
    for job_type in relevant[:3]:
        print("OLDEST_DUE",job_type)
        rows=db.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type==job_type,ProcessingJobModel.status.in_(("pending","retry","processing"))
        ).order_by(ProcessingJobModel.created_at).limit(4)).all()
        for j in rows:
            created=j.created_at.replace(tzinfo=timezone.utc) if j.created_at.tzinfo is None else j.created_at
            due=j.next_attempt_at.replace(tzinfo=timezone.utc) if j.next_attempt_at.tzinfo is None else j.next_attempt_at
            print("state",j.status,"priority",j.priority,"age_min",round((now-created).total_seconds()/60),
                  "due_in_min",round((due-now).total_seconds()/60),
                  "attempt",j.attempt_count,"error",j.last_error_code or "none")
    print("PROCESSING_POLICIES")
    for row in db.scalars(select(TenantProcessingPolicyModel).limit(12)).all():
        print("tenant", row.tenant_id, "ai_analysis_enabled", row.ai_analysis_enabled,
              "max_concurrent_ai", getattr(row,"max_concurrent_ai",None))
