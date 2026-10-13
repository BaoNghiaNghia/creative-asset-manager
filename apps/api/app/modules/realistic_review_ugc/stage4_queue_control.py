"""Durable tenant-scoped pause for Stage 4 image generation.

Running external requests are allowed to finish to preserve generated artwork.
Queued work stays intact until the operator resumes the worker lane.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import ProcessingPolicyAuditModel
from app.modules.realistic_review_ugc.model import (
    RrugcStage2JobModel,
    RrugcStage4QueueControlModel,
)


def stage4_is_paused(session: Session, tenant_id: str) -> bool:
    return bool(session.scalar(select(RrugcStage4QueueControlModel.paused).where(
        RrugcStage4QueueControlModel.tenant_id == tenant_id,
    )))


def stage4_queue_status(session: Session, tenant_id: str) -> dict:
    counts = dict(session.execute(
        select(RrugcStage2JobModel.status, func.count(RrugcStage2JobModel.id))
        .where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            RrugcStage2JobModel.status.in_(("queued", "running")),
        )
        .group_by(RrugcStage2JobModel.status)
    ).all())
    processing_count = int(session.scalar(
        select(func.count(ProcessingJobModel.id)).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "rrugc_stage2_generate",
            ProcessingJobModel.status == "processing",
        )
    ) or 0)
    return {
        "paused": stage4_is_paused(session, tenant_id),
        "queued": int(counts.get("queued", 0)),
        "running": processing_count,
    }


def set_stage4_paused(
    session: Session, *, tenant_id: str, user_id: str, paused: bool,
) -> dict:
    row = session.get(RrugcStage4QueueControlModel, tenant_id)
    was_paused = bool(row.paused) if row is not None else False
    if row is None:
        row = RrugcStage4QueueControlModel(tenant_id=tenant_id)
        session.add(row)
    if paused != was_paused:
        session.add(ProcessingPolicyAuditModel(
            tenant_id=tenant_id,
            actor_id=user_id,
            action="rrugc_stage4_pause" if paused else "rrugc_stage4_resume",
            reason="Stage 4 generation queue manually paused" if paused
                   else "Stage 4 generation queue manually resumed",
            old_policy_json={"stage4_paused": was_paused},
            new_policy_json={"stage4_paused": paused},
        ))
    row.paused = paused
    row.updated_by = user_id
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return stage4_queue_status(session, tenant_id)
